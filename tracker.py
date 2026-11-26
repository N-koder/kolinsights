import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable
from config import TRACK_INTERVAL, MILESTONE_THRESHOLDS, ALERT_CHANNEL_ID
from database import get_active_calls, update_call_price, record_alert
from dex_api import fetch_token_data
from graphics import create_achievement_card

logger = logging.getLogger(__name__)

class PriceTracker:
    def __init__(
        self, 
        alert_callback: Optional[Callable[[dict, float, bytes], Awaitable[None]]] = None,
        consistency_callback: Optional[Callable[[dict, int, list], Awaitable[None]]] = None
    ):
        self.is_running = False
        self.alert_callback = alert_callback
        self.consistency_callback = consistency_callback
        self.hot_task: Optional[asyncio.Task] = None
        self.archival_task: Optional[asyncio.Task] = None

    async def start(self):
        self.is_running = True
        logger.info("🚀 [PRICE TRACKER] Dual-Lane Architecture Active: Lane 1 (Hot Turbo 1s) + Lane 2 (Gentle Archival Sweeper).")
        self.hot_task = asyncio.create_task(self._hot_tracker_loop())
        self.archival_task = asyncio.create_task(self._archival_tracker_loop())
        await asyncio.gather(self.hot_task, self.archival_task, return_exceptions=True)

    def stop(self):
        self.is_running = False
        if self.hot_task and not self.hot_task.done():
            self.hot_task.cancel()
        if self.archival_task and not self.archival_task.done():
            self.archival_task.cancel()
        logger.info("PriceTracker stopped.")

    async def process_single_call(self, call: dict, pre_fetched_data: Optional[dict] = None):
        try:
            token_data = pre_fetched_data
            if not token_data or token_data.get("market_cap", 0) <= 0:
                return None

            current_mc = token_data["market_cap"]
            entry_mc = call["entry_mc"]
            if not entry_mc or entry_mc < 100:
                logger.debug(f"Skipping tracking for call #{call['id']} with anomalous/dust entry MC: {entry_mc}")
                return None

            # Auto-Update Call Metadata if previously recorded as UNKNOWN / placeholder
            real_symbol = token_data.get("symbol")
            real_name = token_data.get("name")
            real_chain = token_data.get("chain")
            if real_symbol and real_symbol != "UNKNOWN" and (call.get("token_symbol") == "UNKNOWN" or call.get("token_name") == "New Launch"):
                from database import update_call_metadata
                logger.info(f"✨ [NAME AUTO-RESOLVED] Call #{call['id']} resolved real metadata: {real_name} (${real_symbol}) on {real_chain.upper()}")
                await update_call_metadata(call["id"], real_symbol, real_name, real_chain)
                call["token_symbol"] = real_symbol
                call["token_name"] = real_name
                call["chain"] = real_chain

            # Calculate call age in hours
            from datetime import datetime, timezone
            call_created = call.get("created_at")
            age_hours = 0.0
            if call_created:
                try:
                    created_str = str(call_created)[:19]
                    created_dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                    age_hours = (now_dt - created_dt).total_seconds() / 3600.0
                except Exception:
                    pass

            # Auto-Drop Filter: Mark dead/rugged tokens in database so they are permanently excluded from future scanning
            from database import mark_call_status
            if current_mc < 300 and age_hours > 24.0:
                logger.info(f"🧹 [AUTO-DROP DEAD] Call #{call['id']} ({call['token_symbol']}) Market Cap ${current_mc:,.0f} < $300 after 24h. Auto-dropping...")
                await mark_call_status(call["id"], "dead")
                return None

            prev_ath = call.get("ath_mc", 0) or 0
            
            # Anomaly Protection: Ignore single-tick market cap spikes (e.g. 20x jump in 30s) if volume/liquidity is unverified
            if prev_ath > 0 and current_mc > (prev_ath * 20) and current_mc > 500_000:
                logger.warning(f"⚠️ PriceTracker ignored single-tick anomaly for call #{call['id']} ({call['token_symbol']}): current MC=${current_mc:,.0f} vs prev ATH=${prev_ath:,.0f}")
                current_mc = prev_ath * 2

            # True Live Real-Time ATH Tracker (Strictly tracks post-call price progression from entry forward)
            ath_mc = max(prev_ath, current_mc)
            highest_multiplier = ath_mc / entry_mc
            last_alert_mult = call.get("last_alert_multiplier", 1.0) or 1.0

            # Collect all crossed, un-alerted milestones in ascending order
            eff_mult = round(highest_multiplier + 0.0001, 2)
            crossed_milestones = [
                m for m in MILESTONE_THRESHOLDS 
                if eff_mult >= m > last_alert_mult
            ]

            # Highest Peak Alert Mode: If a call surges across multiple un-alerted milestones in a single cycle, record intermediate milestones in DB and post ONLY the highest peak milestone to Telegram!
            if len(crossed_milestones) > 1:
                for m in crossed_milestones[:-1]:
                    await record_alert(call["id"], m)
                crossed_milestones = [crossed_milestones[-1]]

            current_last_alert = last_alert_mult

            for new_milestone in crossed_milestones:
                mult_str = f"x{int(new_milestone)}" if new_milestone.is_integer() else f"x{new_milestone:.1f}"
                logger.info(f"🚀 Call #{call['id']} hit milestone {mult_str}! ({call['token_symbol']})")

                # Calculate exact proportional market cap for this specific milestone
                milestone_mc = ath_mc if new_milestone == crossed_milestones[-1] else (new_milestone * entry_mc)
                milestone_call = dict(call)
                milestone_call["ath_mc"] = milestone_mc
                if token_data:
                    milestone_call["pair_address"] = token_data.get("pair_address", "")
                    milestone_call["pair_url"] = token_data.get("pair_url", "")

                card_buffer = await asyncio.to_thread(
                    create_achievement_card,
                    multiplier_text=mult_str,
                    channel_name=call["channel_username"] or call["channel_title"],
                    token_name=call["token_name"],
                    entry_mc=entry_mc,
                    current_mc=milestone_mc,
                    chain=call["chain"]
                )

                if self.alert_callback:
                    try:
                        await self.alert_callback(milestone_call, new_milestone, card_buffer.getvalue())
                        await record_alert(call["id"], new_milestone)
                        current_last_alert = new_milestone
                    except Exception as alert_err:
                        logger.error(f"Alert delivery failed for call #{call['id']} milestone {mult_str}: {alert_err}")
                        break
                    finally:
                        try:
                            card_buffer.close()
                        except Exception:
                            pass
                        del card_buffer

                # Check for Consistency Alert (e.g. 5 x2+ calls in past 7 days)
                if self.consistency_callback and new_milestone >= 2.0 and call.get("channel_id"):
                    from database import get_caller_x2_calls_7d, record_consistency_alert
                    x2_calls = await get_caller_x2_calls_7d(call["channel_id"])
                    count = len(x2_calls)
                    if count >= 5 and (count % 5 == 0 or count == 5):
                        if await record_consistency_alert(call["channel_id"], count):
                            try:
                                await self.consistency_callback(call, count, x2_calls)
                            except Exception as c_err:
                                logger.error(f"Consistency alert callback failed for channel {call['channel_id']}: {c_err}")

            return {
                "call_id": call["id"],
                "current_mc": current_mc,
                "ath_mc": ath_mc,
                "highest_multiplier": highest_multiplier,
                "last_alert_multiplier": current_last_alert
            }

        except Exception as e:
            logger.error(f"Error checking price for call #{call.get('id')}: {e}")
            return None

    async def _hot_tracker_loop(self):
        """Lane 1: Dedicated 1-second continuous cycle for live calls (<= 2 hours old). Zero queueing, sub-second latency."""
        from dex_api import fetch_tokens_batch
        from database import update_calls_price_batch, get_hot_active_calls
        import gc

        loop_count = 0
        while self.is_running:
            try:
                loop_count += 1
                start_t = time.time()
                
                # Fetch ONLY hot calls directly from SQL (<= 2h old, max 50 calls) - 0 RAM waste!
                hot_calls = await get_hot_active_calls(hours=2, limit=50)

                if hot_calls:
                    all_addrs = list(set(c["token_address"] for c in hot_calls if c.get("token_address")))
                    # 1 single batch HTTP request (up to 30 hot tokens)
                    batch_results = await fetch_tokens_batch(all_addrs[:30]) if all_addrs else {}

                    async def _proc(c):
                        t_addr = (c.get("token_address") or "").lower().strip()
                        data = batch_results.get(t_addr)
                        return await self.process_single_call(c, pre_fetched_data=data)

                    db_updates = await asyncio.gather(*(_proc(c) for c in hot_calls))
                    valid_updates = [u for u in db_updates if u]
                    if valid_updates:
                        await update_calls_price_batch(valid_updates)

                    elapsed = time.time() - start_t
                    sym_list = ", ".join(set(c.get("token_symbol", "") for c in hot_calls[:6]))
                    if loop_count % 5 == 0 or elapsed > 1.5:
                        logger.info(f"🔥 [HOT TURBO LANE] Swept {len(hot_calls)} live calls in {elapsed:.2f}s | Active tokens: [{sym_list}]")

                # Periodic garbage collection to maintain ultra-low RAM footprint (<100MB)
                if loop_count % 30 == 0:
                    gc.collect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Hot Turbo Tracker loop: {e}", exc_info=True)

            await asyncio.sleep(2.5)

    async def _archival_tracker_loop(self):
        """Lane 2: Gentle background sweeper for older calls (2h to 14 days). Slices of 30 tokens every 5s."""
        from dex_api import fetch_tokens_batch
        from database import update_calls_price_batch, get_archival_active_calls_slice, get_archival_active_calls_count
        import gc

        slice_offset = 0
        loop_count = 0
        while self.is_running:
            try:
                loop_count += 1
                start_t = time.time()
                
                # Query total count directly in SQL without loading rows into RAM
                total_cold = await get_archival_active_calls_count(hours_cutoff=2, days=14)
                if total_cold > 0:
                    if slice_offset >= total_cold:
                        slice_offset = 0
                        logger.info(f"❄️ [ARCHIVAL SWEEPER] Completed full sweep of {total_cold} older calls. Resting 45s before next cycle...")
                        await asyncio.sleep(45.0)
                        continue

                    # Fetch ONLY the current 30-call slice directly from SQL
                    batch_slice = await get_archival_active_calls_slice(hours_cutoff=2, days=14, offset=slice_offset, limit=30)
                    slice_offset += 30

                    if batch_slice:
                        all_addrs = list(set(c["token_address"] for c in batch_slice if c.get("token_address")))
                        batch_results = await fetch_tokens_batch(all_addrs) if all_addrs else {}

                        async def _proc(c):
                            t_addr = (c.get("token_address") or "").lower().strip()
                            data = batch_results.get(t_addr)
                            return await self.process_single_call(c, pre_fetched_data=data)

                        db_updates = await asyncio.gather(*(_proc(c) for c in batch_slice))
                        valid_updates = [u for u in db_updates if u]
                        if valid_updates:
                            await update_calls_price_batch(valid_updates)

                        elapsed = time.time() - start_t
                        logger.info(f"❄️ [ARCHIVAL SWEEPER] Checked slice of {len(batch_slice)}/{total_cold} older calls in {elapsed:.2f}s | Progress: {min(slice_offset, total_cold)}/{total_cold}")
                else:
                    await asyncio.sleep(15.0)
                    continue

                if loop_count % 20 == 0:
                    gc.collect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Archival Sweeper loop: {e}", exc_info=True)

            await asyncio.sleep(10.0)
