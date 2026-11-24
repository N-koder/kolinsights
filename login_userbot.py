import sys, os, asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

api_id = os.getenv("TG_API_ID")
api_hash = os.getenv("TG_API_HASH")

if not api_id or not api_hash:
    print("❌ ERROR: TG_API_ID or TG_API_HASH is missing in .env file!")
    sys.exit(1)

async def main():
    print("=" * 60)
    print("  KOLInsights Telethon Userbot Interactive Login Helper")
    print("=" * 60)
    print(f"Using TG_API_ID: {api_id}")
    print(f"Using TG_API_HASH: {api_hash}")
    print("-" * 60)

    session_name = "alpharadar_userbot"
    session_file = f"{session_name}.session"

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        session_str = StringSession.save(client.session)
        print("\n✅ ALREADY AUTHORIZED!")
        print(f" - Account Name: {me.first_name} {me.last_name or ''}")
        print(f" - Username: @{me.username}")
        print(f" - Phone: +{me.phone}")
        print(f" - Telegram Premium: {getattr(me, 'premium', False)}")
        print("\n" + "=" * 60)
        print("✅ SUCCESS! Here is your TG_SESSION_STRING:")
        print("=" * 60)
        print(session_str)
        print("=" * 60)
        print("\n👉 Next steps:")
        print("1. Copy the string above.")
        print("2. Set TG_SESSION_STRING=<the_string> in your .env or Render Environment Variables.")
        print("3. Keep this string secret! Anyone with this string can access your Telegram account.")
        await client.disconnect()
        return

    phone = input("\n📱 Enter client's phone number with country code (e.g. +919811281290): ").strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    print(f"\nSending OTP code to Telegram app for {phone}...")
    sent = await client.send_code_request(phone)
    print(f"✅ OTP Code sent! (phone_code_hash: {sent.phone_code_hash[:8]}...)")

    code = input("\n🔑 Enter the OTP code received in Telegram: ").strip()

    try:
        me = await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except Exception as e:
        if "Two-steps verification" in str(e) or "2FA" in str(e) or "PASSWORD_HASH_INVALID" in str(e) or "SessionPasswordNeeded" in str(type(e)):
            password = input("\n🔒 Enter 2FA Cloud Password: ").strip()
            me = await client.sign_in(password=password)
        else:
            raise e

    session_str = StringSession.save(client.session)

    print("\n🎉 SUCCESS! Logged in successfully!")
    print(f" - Account Name: {me.first_name} {me.last_name or ''}")
    print(f" - Username: @{me.username}")
    print(f" - Phone: +{me.phone}")
    print(f" - Telegram Premium: {getattr(me, 'premium', False)}")

    print("\n" + "=" * 60)
    print("✅ SUCCESS! Here is your TG_SESSION_STRING:")
    print("=" * 60)
    print(session_str)
    print("=" * 60)
    print("\n👉 Next steps:")
    print("1. Copy the string above.")
    print("2. Set TG_SESSION_STRING=<the_string> in your .env or Render Environment Variables.")
    print("3. Keep this string secret! Anyone with this string can access your Telegram account.")

    await client.disconnect()
    print(f"\n✅ Session saved to '{session_file}'. You can now run 'python main.py'!")

if __name__ == "__main__":
    asyncio.run(main())
