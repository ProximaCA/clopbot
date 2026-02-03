"""Tool for importing channel history from Telegram export JSON."""

import json
from pathlib import Path
from loguru import logger
from nanobot.agent.tools.base import Tool


class ImportChannelHistoryTool(Tool):
    """Import channel posts from Telegram export JSON into channel history."""
    
    name = "import_channel_history"
    description = (
        "Import channel post history from a Telegram export JSON file (result.json). "
        "Use this to populate channel_history.jsonl with past posts for context. "
        "Only admin can use this tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the Telegram export JSON file (result.json)"
            }
        },
        "required": ["file_path"]
    }
    
    def __init__(self, workspace: Path, admin_ids: list[str]):
        """Initialize with workspace path and admin IDs."""
        self.workspace = workspace
        self.admin_ids = admin_ids
        self.histories_dir = workspace / "channel_histories"
        self.histories_dir.mkdir(exist_ok=True)
        self._is_admin = False
        self._current_chat_id = None
    
    def set_chat_context(self, chat_id: str):
        """Set current chat ID for context isolation."""
        self._current_chat_id = chat_id
    
    def _get_history_file(self) -> Path:
        """Get history file for current chat."""
        if not self._current_chat_id:
            # Fallback to default
            return self.workspace / "channel_history.jsonl"
        
        # Sanitize chat_id for filename
        safe_chat_id = str(self._current_chat_id).replace("-", "neg").replace("|", "_")
        return self.histories_dir / f"chat_{safe_chat_id}.jsonl"
    
    def set_user_context(self, user_id: str, is_admin: bool):
        """Set current user context (called by agent loop)."""
        self._is_admin = is_admin
    
    async def execute(self, file_path: str) -> str:
        """Import channel history from JSON file."""
        
        # CRITICAL: Admin-only check
        if not self._is_admin:
            logger.warning(f"Non-admin attempted to use import_channel_history")
            return "Error: This command is only available to the bot owner (admin)."
        
        try:
            path = Path(file_path)
            if not path.exists():
                return f"Error: File not found at {file_path}"
            
            # Read JSON export
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, dict) or "messages" not in data:
                return "Error: Invalid Telegram export format. Expected 'messages' field."
            
            messages = data["messages"]
            channel_name = data.get("name", "Unknown Channel")
            
            # Filter for actual posts (not service messages)
            posts = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                
                if msg.get("type") == "service":
                    continue
                
                # Extract text
                text = msg.get("text", "")
                
                # Handle if text is a list (Telegram sometimes exports text as array of strings)
                if isinstance(text, list):
                    text = "".join(str(t) for t in text)
                
                # If text is empty or just formatting objects, try text_entities
                if not text and "text_entities" in msg:
                    text_parts = []
                    for entity in msg["text_entities"]:
                        if isinstance(entity, dict) and "text" in entity:
                            text_parts.append(str(entity["text"]))
                    text = "".join(text_parts)
                
                # Convert to string and validate
                text = str(text) if text else ""
                if not text.strip():
                    continue
                
                # Create entry
                post_id = msg.get("id", 0)
                date = msg.get("date", "Unknown")
                from_user = msg.get("from", channel_name)
                
                entry = {
                    "id": post_id,
                    "date": date,
                    "from": from_user,
                    "content": text,
                    "timestamp": msg.get("date", "")
                }
                posts.append(entry)
            
            if not posts:
                return "No valid posts found in the export file."
            
            # Write to chat-specific history file (append mode)
            history_file = self._get_history_file()
            imported_count = 0
            
            with open(history_file, 'a', encoding='utf-8') as f:
                for post in posts:
                    f.write(json.dumps(post, ensure_ascii=False) + '\n')
                    imported_count += 1
            
            chat_context = f" for chat {self._current_chat_id}" if self._current_chat_id else ""
            logger.info(f"Imported {imported_count} posts to channel history{chat_context} from {file_path}")
            return f"✅ Successfully imported {imported_count} posts from {channel_name} into channel history{chat_context}!"
            
        except json.JSONDecodeError:
            return "Error: Invalid JSON file."
        except Exception as e:
            logger.error(f"Failed to import channel history: {e}")
            return f"Error importing channel history: {str(e)}"
