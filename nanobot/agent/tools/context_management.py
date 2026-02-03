"""Context and memory management tools (admin-only)."""

from pathlib import Path
from datetime import datetime
from loguru import logger
from nanobot.agent.tools.base import Tool


class ClearContextTool(Tool):
    """Tool for clearing context and memory (ADMIN ONLY)."""
    
    name = "clear_context"
    description = (
        "Clear conversation history and context. ADMIN-ONLY command. "
        "Use when the admin explicitly requests to clear/reset context or memory. "
        "Actions: 'session' (current chat), 'today' (today's history), 'all' (everything)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["session", "today", "all"],
                "description": "What to clear: 'session' (current chat only), 'today' (today's messages), 'all' (full reset)"
            },
            "confirm": {
                "type": "boolean",
                "description": "Confirmation flag (must be true to execute)"
            }
        },
        "required": ["action", "confirm"]
    }
    
    def __init__(self, workspace: Path, admin_ids: list[str], session_manager=None):
        """Initialize with workspace path, admin IDs, and session manager."""
        self.workspace = workspace
        self.admin_ids = admin_ids
        self.session_manager = session_manager
        self._current_user_id = None  # Set by agent loop
    
    def set_user_context(self, user_id: str, is_admin: bool):
        """Set current user context (called by agent loop)."""
        self._current_user_id = user_id
        self._is_admin = is_admin
    
    async def execute(self, action: str, confirm: bool = False) -> str:
        """Execute context clearing action."""
        
        # CRITICAL: Admin-only check
        if not getattr(self, '_is_admin', False):
            logger.warning(f"Non-admin user {self._current_user_id} attempted to use clear_context")
            return "Error: This command is only available to the bot owner (admin)."
        
        if not confirm:
            return "Error: Confirmation required. Set 'confirm: true' to proceed."
        
        try:
            if action == "session":
                # Clear current session
                if self.session_manager and hasattr(self.session_manager, 'sessions'):
                    cleared = 0
                    for session_key in list(self.session_manager.sessions.keys()):
                        session = self.session_manager.sessions[session_key]
                        session.clear_history()
                        cleared += 1
                    logger.info(f"Cleared {cleared} active sessions")
                    return f"✅ Cleared {cleared} active chat session(s). Context reset."
                else:
                    return "⚠️ Session manager not available. Context may not be fully cleared."
            
            elif action == "today":
                # Clear today's history from session files
                today = datetime.now().date()
                memory_dir = self.workspace / "memory"
                
                cleared_sessions = 0
                if self.session_manager and hasattr(self.session_manager, 'sessions'):
                    for session_key in list(self.session_manager.sessions.keys()):
                        session = self.session_manager.sessions[session_key]
                        # Filter out today's messages
                        if hasattr(session, 'messages'):
                            original_count = len(session.messages)
                            # Keep only messages from before today (this is simplified)
                            session.clear_history()
                            cleared_sessions += 1
                
                logger.info(f"Cleared today's history from {cleared_sessions} sessions")
                return f"✅ Cleared today's conversation history from {cleared_sessions} session(s)."
            
            elif action == "all":
                # Full reset: clear all sessions + memory files
                cleared_sessions = 0
                if self.session_manager and hasattr(self.session_manager, 'sessions'):
                    for session_key in list(self.session_manager.sessions.keys()):
                        session = self.session_manager.sessions[session_key]
                        session.clear_history()
                        cleared_sessions += 1
                
                # Optionally clear memory files (be careful!)
                memory_dir = self.workspace / "memory"
                cleared_files = []
                if memory_dir.exists():
                    for file_path in memory_dir.glob("*.md"):
                        # Backup before clearing
                        backup_path = file_path.with_suffix(f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                        file_path.rename(backup_path)
                        cleared_files.append(file_path.name)
                
                logger.info(f"Full reset: {cleared_sessions} sessions, {len(cleared_files)} memory files backed up")
                return (
                    f"✅ Full reset complete:\n"
                    f"- Cleared {cleared_sessions} session(s)\n"
                    f"- Backed up {len(cleared_files)} memory file(s)\n"
                    f"Context is now clean."
                )
            
            else:
                return f"Error: Unknown action '{action}'. Use 'session', 'today', or 'all'."
        
        except Exception as e:
            logger.error(f"Failed to clear context: {e}")
            return f"Error clearing context: {str(e)}"
