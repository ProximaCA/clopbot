"""Tool for reading channel post history."""

import json
from pathlib import Path
from loguru import logger
from nanobot.agent.tools.base import Tool


class ReadChannelHistoryTool(Tool):
    """Tool for reading channel post history to understand context."""
    
    name = "read_channel_history"
    description = (
        "Read recent posts from the channel to understand context and reference specific posts. "
        "Use this when analyzing the channel or when asked about specific posts/thoughts. "
        "Returns recent posts with their content and metadata."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of recent posts to return (default: 20, max: 50)",
                "default": 20
            },
            "search": {
                "type": "string",
                "description": "Optional search term to filter posts (e.g., 'tonify', 'degen')"
            }
        },
        "required": []
    }
    
    def __init__(self, workspace: Path):
        """Initialize with workspace path."""
        self.workspace = workspace
        self.histories_dir = workspace / "channel_histories"
        self.histories_dir.mkdir(exist_ok=True)
        
        # Current chat context (set by agent loop)
        self._current_chat_id = None
    
    def set_chat_context(self, chat_id: str):
        """Set current chat ID for context isolation."""
        self._current_chat_id = chat_id
    
    def _get_history_file(self) -> Path:
        """Get history file for current chat."""
        if not self._current_chat_id:
            # Fallback to default for backward compatibility
            return self.workspace / "channel_history.jsonl"
        
        # Sanitize chat_id for filename (remove special chars)
        safe_chat_id = str(self._current_chat_id).replace("-", "neg").replace("|", "_")
        return self.histories_dir / f"chat_{safe_chat_id}.jsonl"
    
    async def execute(self, limit: int = 20, search: str = None) -> str:
        """Read channel post history for current chat."""
        try:
            history_file = self._get_history_file()
            
            if not history_file.exists():
                chat_context = f" for this chat" if self._current_chat_id else ""
                return f"No channel history available yet{chat_context}. History is built as posts are received."
            
            # Read all posts from JSONL file
            posts = []
            with open(history_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            post = json.loads(line)
                            posts.append(post)
                        except json.JSONDecodeError:
                            continue
            
            if not posts:
                return "No posts found in channel history."
            
            # Filter by search term if provided
            if search:
                search_lower = search.lower()
                posts = [p for p in posts if search_lower in p.get("content", "").lower()]
                
                if not posts:
                    return f"No posts found matching '{search}'."
            
            # Get most recent posts (JSONL is append-only, so last entries are newest)
            recent_posts = posts[-min(limit, len(posts)):]
            
            # Format output
            output = [f"## Recent Channel Posts ({len(recent_posts)} of {len(posts)} total)"]
            output.append("")
            
            for post in recent_posts:
                date = post.get("date", "Unknown")
                content = post.get("content", "")[:300]  # Truncate long posts
                post_id = post.get("id", "?")
                from_user = post.get("from", "Unknown")
                
                output.append(f"### Post #{post_id} ({date})")
                output.append(f"**From**: {from_user}")
                output.append(f"**Content**: {content}")
                if len(post.get("content", "")) > 300:
                    output.append("*(truncated)*")
                output.append("")
            
            logger.info(f"Retrieved {len(recent_posts)} posts from channel history")
            return "\n".join(output)
            
        except Exception as e:
            logger.error(f"Failed to read channel history: {e}")
            return f"Error reading channel history: {str(e)}"
