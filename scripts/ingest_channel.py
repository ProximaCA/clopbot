import asyncio
import os
import json
from pathlib import Path
from telethon import TelegramClient

# You need to get these from https://my.telegram.org/
# Or set them in environment variables
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

async def main():
    if not API_ID or not API_HASH:
        print("Please set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.")
        return

    channel_username = input("Enter channel username (e.g. @mychannel): ")
    limit_str = input("How many messages to scrape (default 100): ")
    limit = int(limit_str) if limit_str else 100

    async with TelegramClient('nanobot_ingest', API_ID, API_HASH) as client:
        print(f"Scraping {limit} messages from {channel_username}...")
        
        messages = []
        async for message in client.iter_messages(channel_username, limit=limit):
            if message.text:
                messages.append({
                    "id": message.id,
                    "date": message.date.isoformat(),
                    "text": message.text,
                    "views": message.views,
                    "forwards": message.forwards
                })
        
        # Save to file
        output_file = Path("channel_history.json")
        output_file.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {len(messages)} messages to {output_file}")
        
        print("\nGenerating PERSONA.md draft...")
        
        full_text = "\n\n".join([m["text"] for m in messages])
        
        persona_draft = f"""# Learned Persona
        
Based on {len(messages)} messages from {channel_username}.

## Writing Style
- Tone: [Analyze based on content]
- Common topics: [Analyze based on content]
- Vocabulary: [Analyze based on content]

## Sample Content
{full_text[:500]}...
"""
        
        Path("PERSONA_DRAFT.md").write_text(persona_draft, encoding="utf-8")
        print("Created PERSONA_DRAFT.md. Please review and move to workspace/PERSONA.md")

if __name__ == "__main__":
    asyncio.run(main())
