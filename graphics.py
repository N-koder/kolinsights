import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

def format_number(val: float) -> str:
    """Format numbers into human readable strings like $1.6K, $25K, $2.5M, $1.2B."""
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.1f}B"
    elif val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    elif val >= 10_000:
        return f"${val / 1_000:.0f}K"
    elif val >= 1_000:
        k_val = val / 1_000
        formatted = f"{k_val:.1f}".rstrip('0').rstrip('.')
        return f"${formatted}K"
    else:
        return f"${val:.2f}"

import functools

@functools.lru_cache(maxsize=32)
def get_font(size: int, bold: bool = False):
    """Attempts to load high quality truetype fonts on Windows and Linux, with sized default fallbacks (LRU cached)."""
    font_candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except (IOError, OSError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def draw_chain_logo(draw: ImageDraw.ImageDraw, chain: str, cx: int, cy: int, size: int = 36):
    """Draws crisp vector logo icons for popular crypto chains (Solana, Ethereum, BSC, Base, Arbitrum)."""
    c = chain.lower()
    half = size // 2
    if "sol" in c:
        # Solana: 3 parallel diagonal bars
        h = max(3, size // 7)
        draw.polygon([(cx - half + 4, cy - half), (cx + half, cy - half), (cx + half - 4, cy - half + h), (cx - half, cy - half + h)], fill="#00F0FF")
        draw.polygon([(cx - half, cy - h // 2), (cx + half - 4, cy - h // 2), (cx + half, cy + h // 2), (cx - half + 4, cy + h // 2)], fill="#A855F7")
        draw.polygon([(cx - half + 4, cy + half - h), (cx + half, cy + half - h), (cx + half - 4, cy + half), (cx - half, cy + half)], fill="#00FF9D")
    elif "eth" in c:
        # Ethereum: Diamond pyramid
        draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + 4), (cx - half, cy)], fill="#627EEA")
        draw.polygon([(cx, cy - half), (cx, cy + 4), (cx - half, cy)], fill="#8C9EFF")
        draw.polygon([(cx, cy + 6), (cx + half, cy + 2), (cx, cy + half), (cx - half, cy + 2)], fill="#C0C9EE")
    elif "bsc" in c or "binance" in c:
        # BSC: Golden Diamond lattice
        draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)], fill="#F3BA2F")
        draw.polygon([(cx, cy - half // 2), (cx + half // 2, cy), (cx, cy + half // 2), (cx - half // 2, cy)], fill="#1E2329")
    elif "base" in c:
        # Base: Blue Circle with Core
        draw.ellipse([cx - half, cy - half, cx + half, cy + half], fill="#0052FF")
        draw.ellipse([cx - half + 6, cy - half + 6, cx + half - 6, cy + half - 6], fill="#FFFFFF")
    else:
        # Generic Crypto Diamond Star
        draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)], fill="#00F0FF")

def create_achievement_card(
    multiplier_text: str,
    channel_name: str,
    token_name: str,
    entry_mc: float,
    current_mc: float,
    chain: str = "solana",
    theme: str = "cyber_indigo"
) -> BytesIO:
    """
    Renders live milestone achievement metrics on top of user's 3D glass template image.
    Falls back to synthetic canvas if template is missing.
    Returns a BytesIO PNG buffer.
    """
    template_path = os.path.join(os.path.dirname(__file__), "default_template.jpg")
    
    img = None
    draw = None
    try:
        if os.path.exists(template_path):
            img = Image.open(template_path).convert("RGB")
            draw = ImageDraw.Draw(img)

            font_hdr = get_font(22, bold=True)
            font_badge = get_font(32, bold=True)
            font_caller = get_font(34, bold=True)
            font_desc = get_font(22, bold=False)
            font_mc_lbl = get_font(17, bold=True)
            font_mc_val = get_font(38, bold=True)
            font_gain = get_font(18, bold=True)
            font_ftr = get_font(16, bold=True)

            # 1. Top Header: KOLINSIGHTS ANALYTICS
            draw.rectangle([290, 60, 735, 92], fill="#0A1C2C")
            draw.text((512, 76), "KOLINSIGHTS ANALYTICS", fill="#00F0FF", font=font_hdr, anchor="mm")

            try:
                val_num = float(multiplier_text.lower().replace("x", ""))
                alpha_label = " (ALPHA CALL)" if 100.0 <= val_num <= 500.0 else ""
            except ValueError:
                alpha_label = ""

            # 2. Hero Milestone Title: ACHIEVEMENT UNLOCKED: X3.0!
            draw.rectangle([180, 138, 845, 205], fill="#113654")
            draw.text((512, 172), f"ACHIEVEMENT UNLOCKED: {multiplier_text.upper()}{alpha_label}!", fill="#00F0FF", font=font_badge, anchor="mm")

            ch_display = f"@{channel_name}" if not channel_name.startswith("@") else channel_name

            # Dynamic font scaling for long handles or token names
            if len(ch_display) > 20:
                font_caller_dyn = get_font(26, bold=True)
            else:
                font_caller_dyn = font_caller

            call_subline = f"made a {multiplier_text} call on {token_name}"
            if len(call_subline) > 35:
                font_desc_dyn = get_font(18, bold=False)
            else:
                font_desc_dyn = font_desc

            # 3. Caller Handle: @WCTCalls
            draw.rectangle([350, 222, 670, 268], fill="#0A2238")
            draw.text((512, 244), ch_display, fill="#00F0FF", font=font_caller_dyn, anchor="mm")

            # 4. Call Subline: made a x3.0 call on AnthropicAI
            draw.rectangle([200, 272, 820, 310], fill="#091A2A")
            draw.text((512, 290), call_subline, fill="#FFFFFF", font=font_desc_dyn, anchor="mm")

            # 5. Left Box: ENTRY MARKET CAP & $848K
            entry_str = format_number(entry_mc)
            current_str = format_number(current_mc)
            gain_mc = current_mc - entry_mc
            gain_pct = ((current_mc / entry_mc) - 1) * 100 if entry_mc > 0 else 0

            draw.rectangle([140, 350, 395, 375], fill="#12253A")
            draw.text((267, 362), "ENTRY MARKET CAP", fill="#94A3B8", font=font_mc_lbl, anchor="mm")

            draw.rectangle([150, 378, 385, 432], fill="#091828")
            draw.text((267, 405), entry_str, fill="#FFFFFF", font=font_mc_val, anchor="mm")

            # 6. Right Box: PEAK ATH MARKET CAP & $2.5M
            draw.rectangle([625, 350, 880, 375], fill="#12253A")
            draw.text((752, 362), "PEAK ATH MARKET CAP", fill="#94A3B8", font=font_mc_lbl, anchor="mm")

            draw.rectangle([635, 378, 870, 432], fill="#091828")
            draw.text((752, 405), current_str, fill="#00FF9D", font=font_mc_val, anchor="mm")

            # 7. Sub-pill: TOTAL GAIN: +$1.7M (+195%)
            gain_str = f"TOTAL GAIN: +{format_number(gain_mc)} (+{gain_pct:.0f}%)"
            draw.rectangle([300, 442, 725, 472], fill="#0A1826")
            draw.text((512, 457), gain_str, fill="#00FF9D", font=font_gain, anchor="mm")

            # 8. Footer Left & Right
            draw.rectangle([45, 528, 380, 565], fill="#05111E")
            draw.text((95, 545), f"NETWORK: {chain.upper()}", fill="#94A3B8", font=font_ftr, anchor="lm")

            draw.rectangle([500, 528, 975, 565], fill="#05111E")
            draw.text((920, 545), "POWERED BY KOLINSIGHTS", fill="#94A3B8", font=font_ftr, anchor="rm")
        else:
            width, height = 1200, 675
            img = Image.new("RGB", (width, height), color="#090B10")
            draw = ImageDraw.Draw(img)

            font_header = get_font(34, bold=True)
            font_badge = get_font(52, bold=True)
            font_caller = get_font(42, bold=True)
            font_desc = get_font(34, bold=False)
            font_mc_lbl = get_font(24, bold=True)
            font_mc_val = get_font(52, bold=True)
            font_gain = get_font(28, bold=True)
            font_ftr = get_font(24, bold=True)

            draw_chain_logo(draw, chain, 430, 70, size=28)
            draw.text((610, 70), "KOLINSIGHTS ANALYTICS", fill="#F59E0B", font=font_header, anchor="mm")
            draw.text((width // 2, 160), f"ACHIEVEMENT UNLOCKED: {multiplier_text.upper()}{alpha_label}!", fill="#00F0FF", font=font_badge, anchor="mm")

            ch_display = f"@{channel_name}" if not channel_name.startswith("@") else channel_name
            draw.text((width // 2, 260), ch_display, fill="#38BDF8", font=font_caller, anchor="mm")
            draw.text((width // 2, 315), f"made a {multiplier_text} call on {token_name}", fill="#F8FAFC", font=font_desc, anchor="mm")

            draw.rounded_rectangle([120, 375, 1080, 545], radius=24, fill="#121722", outline="#1E293B", width=2)
            entry_str = format_number(entry_mc)
            current_str = format_number(current_mc)
            gain_mc = current_mc - entry_mc
            gain_pct = ((current_mc / entry_mc) - 1) * 100 if entry_mc > 0 else 0

            draw.text((310, 415), "ENTRY MARKET CAP", fill="#94A3B8", font=font_mc_lbl, anchor="mm")
            draw.text((310, 465), entry_str, fill="#FFFFFF", font=font_mc_val, anchor="mm")
            draw.text((width // 2, 445), "->", fill="#00F0FF", font=font_mc_val, anchor="mm")
            draw.text((890, 415), "PEAK ATH MARKET CAP", fill="#94A3B8", font=font_mc_lbl, anchor="mm")
            draw.text((880, 465), current_str, fill="#10B981", font=font_mc_val, anchor="mm")

            gain_str = f"TOTAL GAIN: +{format_number(gain_mc)} (+{gain_pct:.0f}%)"
            draw.rounded_rectangle([350, 502, 850, 536], radius=15, fill="#064E3B")
            draw.text((width // 2, 519), gain_str, fill="#34D399", font=font_gain, anchor="mm")

            chain_str = f"NETWORK: {chain.upper()}"
            draw_chain_logo(draw, chain, 75, height - 45, size=28)
            draw.text((98, height - 45), chain_str, fill="#64748B", font=font_ftr, anchor="lm")
            draw.text((width - 60, height - 45), "POWERED BY KOLINSIGHTS", fill="#64748B", font=font_ftr, anchor="rm")

        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return buffer
    finally:
        if draw is not None:
            del draw
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
            del img
