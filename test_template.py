import os
import shutil
from PIL import Image, ImageDraw, ImageFont
from graphics import get_font, format_number

def render_template_card(multiplier_text: str, channel_name: str, token_name: str, entry_mc: float, current_mc: float, chain: str = "solana"):
    template_path = "default_template.jpg"
    if not os.path.exists(template_path):
        template_path = "test_achievement.png"

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

    # 2. Hero Milestone Title: ACHIEVEMENT UNLOCKED: X3.0!
    draw.rectangle([180, 138, 845, 205], fill="#113654")
    draw.text((512, 172), f"ACHIEVEMENT UNLOCKED: {multiplier_text.upper()}!", fill="#00F0FF", font=font_badge, anchor="mm")

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

    return img

if __name__ == "__main__":
    img = render_template_card("x3.0", "WCTCalls", "AnthropicAI", 848000.0, 2500000.0, "solana")
    img.save("test_template_card.png", quality=95)
    artifact_path = r"C:\Users\webwo\.gemini\antigravity-ide\brain\4f1bc9ce-8d28-48df-9bdc-d2a4b0821d5f\test_template_card.png"
    shutil.copy("test_template_card.png", artifact_path)
    print("Generated and saved to artifact path:", artifact_path)
