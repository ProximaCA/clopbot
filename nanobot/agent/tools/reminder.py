"""Tool for creating reminders using cron system."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from nanobot.agent.tools.base import Tool


class ReminderTool(Tool):
    """Tool for creating time-based reminders."""
    
    name = "create_reminder"
    description = (
        "CRITICAL: Create a time-based reminder to send a message later. "
        "MUST be used when user says: 'напомни', 'remind', 'напомнить', 'в N часов'. "
        "Supports natural language time: "
        "'вечером' (18:00), 'утром' (9:00), 'через N часов' (in N hours), "
        "'завтра' (tomorrow 9:00), '15:00', '3pm', etc. "
        "Message can include @mentions. "
        "Examples: "
        "напомни @user выпустить подкаст вечером → create_reminder(message='@user выпустить подкаст', when='вечером')"
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The reminder message to send (including mentions like @username)"
            },
            "when": {
                "type": "string",
                "description": "When to send: 'вечером', 'утром', 'через 2 часа', 'завтра', '15:00', etc."
            },
            "chat_id": {
                "type": "string",
                "description": "Optional chat ID where to send (defaults to current chat)",
                "default": None
            }
        },
        "required": ["message", "when"]
    }
    
    def __init__(self, workspace: Path, cron_service=None):
        """Initialize with workspace and cron service."""
        self.workspace = workspace
        self.cron_service = cron_service
        self._current_chat_id = None
    
    def set_chat_context(self, chat_id: str):
        """Set current chat ID for context."""
        self._current_chat_id = chat_id
    
    def _parse_time(self, when: str) -> datetime | None:
        """Parse natural language time to datetime."""
        now = datetime.now()
        when_lower = when.lower().strip()
        
        # Evening (18:00)
        if "вечер" in when_lower or "evening" in when_lower:
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            return target
        
        # Morning (9:00)
        if "утр" in when_lower or "morning" in when_lower:
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            return target
        
        # Tomorrow
        if "завтра" in when_lower or "tomorrow" in when_lower:
            return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        
        # In N hours
        hours_match = re.search(r'через\s+(\d+)\s+час', when_lower)
        if hours_match:
            hours = int(hours_match.group(1))
            return now + timedelta(hours=hours)
        
        # In N minutes
        mins_match = re.search(r'через\s+(\d+)\s+мин', when_lower)
        if mins_match:
            minutes = int(mins_match.group(1))
            return now + timedelta(minutes=minutes)

        # In N seconds
        secs_match = re.search(r'через\s+(\d+)\s+сек', when_lower)
        if secs_match:
            seconds = int(secs_match.group(1))
            return now + timedelta(seconds=seconds)
        
        # Specific time: 15:00, 3pm, etc.
        time_match = re.search(r'(\d{1,2}):(\d{2})', when_lower)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            return target
        
        # Default: in 1 hour
        return now + timedelta(hours=1)
    
    async def execute(self, message: str, when: str, chat_id: str = None) -> str:
        """Create a reminder."""
        try:
            if not self.cron_service:
                return "❌ Reminder system not available (cron service not initialized)."
            
            # Parse time
            target_time = self._parse_time(when)
            if not target_time:
                return f"❌ Could not parse time '{when}'. Use: вечером, утром, через 2 часа, 15:00, etc."
            
            # Get chat ID
            target_chat_id = chat_id or self._current_chat_id
            if not target_chat_id:
                return "❌ No chat ID available. Cannot create reminder."
            
            # Calculate timestamp in milliseconds
            target_ms = int(target_time.timestamp() * 1000)
            
            # Create cron schedule
            from nanobot.cron.types import CronSchedule
            
            schedule = CronSchedule(kind="at", at_ms=target_ms)
            
            # Add to cron service (sync method, not async)
            job = self.cron_service.add_job(
                name=f"Reminder at {target_time.strftime('%H:%M %d.%m')}",
                schedule=schedule,
                message=f"⏰ Reminder:\n\n{message}",
                deliver=True,
                channel="telegram",
                to=target_chat_id,
                delete_after_run=True
            )
            
            job_id = job.id
            
            # Format time for display
            time_str = target_time.strftime("%H:%M %d.%m.%Y")
            
            logger.info(f"Created reminder job {job_id} for {time_str} in chat {target_chat_id}")
            
            return f"✅ Reminder created! I'll send this message at {time_str}:\n\n{message}"
            
        except Exception as e:
            logger.error(f"Failed to create reminder: {e}")
            return f"❌ Failed to create reminder: {str(e)}"
