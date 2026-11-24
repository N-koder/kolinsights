import os, sys, asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageEntityCustomEmoji

env_path = r"c:\Users\webwo\Desktop\Codevault\telegram\spidyfi_bot\.env"
load_dotenv(env_path)

api_id = os.getenv("TG_API_ID")
api_hash = os.getenv("TG_API_HASH")

async def main():
    if not api_id or not api_hash:
        print("TG_API_ID or TG_API_HASH missing in .env")
        return

    client = TelegramClient("alpharadar_userbot", int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Userbot not authorized. Run python login_userbot.py first.")
        return

    print("Fetching last 15 messages from your Saved Messages...")
    print("TIP: Send or forward any custom emoji to 'Saved Messages' in Telegram, then run this script!\n")

    found = 0
    async for msg in client.iter_messages("me", limit=15):
        if msg.entities:
            for entity in msg.entities:
                if isinstance(entity, MessageEntityCustomEmoji):
                    found += 1
                    print(f"FOUND EMOJI -> ID: \"{entity.document_id}\"")

    if found == 0:
        print("No custom emojis found in recent 15 Saved Messages.")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
