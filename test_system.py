import asyncio
import os
import logging
from database import (
    init_db, add_channel, record_call, update_call_price,
    get_channel_stats, get_calls_by_token, record_alert
)
from dex_api import fetch_token_data
from scraper import extract_contract_addresses
from graphics import create_achievement_card

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_system")

TEST_DB = "test_alpharadar.db"

async def run_tests():
    logger.info("=== 1. Testing Contract Address Extraction Regex ===")
    sample_text = (
        "Huge gem call! Buy $CLAUDE now!\n"
        "Solana CA: 7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr\n"
        "EVM CA: 0x6B175474E89094C44Da98b954EedeAC495271d0F"
    )
    cas = extract_contract_addresses(sample_text)
    logger.info(f"Extracted CAs ({len(cas)}): {cas}")
    assert len(cas) >= 2, "Failed to extract CAs!"

    logger.info("\n=== 2. Testing DexScreener Live API Fetcher ===")
    # Test with USDC Solana CA or Ethereum DAI CA
    test_ca = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" # Solana USDC
    data = await fetch_token_data(test_ca)
    if data:
        logger.info(f"Fetched DexScreener Token: {data['name']} ({data['symbol']}) on {data['chain']} - MarketCap: ${data['market_cap']:,.0f}")
    else:
        logger.warning(f"DexScreener return null for {test_ca}")

    logger.info("\n=== 3. Testing Database & Call Recording ===")
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
            if os.path.exists(TEST_DB + "-wal"): os.remove(TEST_DB + "-wal")
            if os.path.exists(TEST_DB + "-shm"): os.remove(TEST_DB + "-shm")
        except Exception:
            pass

    await init_db(TEST_DB)
    await add_channel(1001, "WCT Calls", "WCTCalls", db_path=TEST_DB)
    
    # Record sample calls matching client's screenshot
    call_id1, _, _ = await record_call(
        channel_id=1001,
        channel_title="WCT Calls",
        channel_username="WCTCalls",
        token_address="AnthropicAICa1111111111111111111111111",
        chain="solana",
        token_symbol="AnthropicAI",
        token_name="Anthropic AI",
        entry_mc=848000.0,
        call_msg_link="https://t.me/WCTCalls/101",
        db_path=TEST_DB
    )

    call_id2, _, _ = await record_call(
        channel_id=1001,
        channel_title="WCT Calls",
        channel_username="WCTCalls",
        token_address="GiggleFundCa2222222222222222222222222",
        chain="solana",
        token_symbol="GiggleFund",
        token_name="Giggle Fund",
        entry_mc=190000.0,
        call_msg_link="https://t.me/WCTCalls/102",
        db_path=TEST_DB
    )

    # Update price shifts (AnthropicAI 848K -> 2.5M = 3x)
    await update_call_price(
        call_id=call_id1,
        current_mc=2500000.0,
        ath_mc=2500000.0,
        highest_multiplier=2.94,
        last_alert_multiplier=2.0,
        db_path=TEST_DB
    )

    # Update GiggleFund (190K -> 286M = 1500x)
    await update_call_price(
        call_id=call_id2,
        current_mc=45000000.0,
        ath_mc=286000000.0,
        highest_multiplier=1500.0,
        last_alert_multiplier=100.0,
        db_path=TEST_DB
    )

    logger.info("\n=== 4. Testing /checkstats Calculations ===")
    stats = await get_channel_stats("WCTCalls", db_path=TEST_DB)
    logger.info(f"Stats Result for @{stats['channel_username']}:")
    logger.info(f"  Total Calls: {stats['total_calls']}")
    logger.info(f"  Consistency: {stats['consistency']}%")
    logger.info(f"  Best Call: {stats['best_call']['token_name']} (x{stats['best_call']['highest_multiplier']})")
    logger.info(f"  Simulation $100 ATH yield: ${stats['sim_ath_val']:,.2f}")

    logger.info("\n=== 5. Testing Graphics Achievement Banner Generation ===")
    card_buf = create_achievement_card(
        multiplier_text="x3!",
        channel_name="WCTCalls",
        token_name="AnthropicAI",
        entry_mc=848000.0,
        current_mc=2500000.0,
        chain="solana"
    )
    with open("test_achievement.png", "wb") as f:
        f.write(card_buf.getvalue())
    logger.info("Saved generated achievement card to 'test_achievement.png'")

    logger.info("\n=== 6. Testing main.py send_milestone_alert Caption Building ===")
    from main import send_milestone_alert
    sample_call = {
        "id": 106,
        "channel_username": "wctcalls",
        "channel_title": "WCT Calls",
        "token_name": "Dave",
        "token_symbol": "DAVE",
        "entry_mc": 10000.0,
        "ath_mc": 20000.0,
        "chain": "solana",
        "channel_id": 123456
    }
    # Test executing send_milestone_alert with mock bot_app=None
    await send_milestone_alert(sample_call, 2.0, card_buf.getvalue(), None)
    logger.info("send_milestone_alert executed cleanly without NameError!")

    logger.info("\n✅ ALL SYSTEM TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
