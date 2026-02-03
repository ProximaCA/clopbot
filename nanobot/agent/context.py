"""Context builder for assembling agent prompts."""

import base64
import io
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.persona import PersonaManager


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self.persona = PersonaManager(workspace)
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
        
        Returns:
            Complete system prompt.
        """
        parts = []
        
        # Core identity
        parts.append(self._get_identity())
        
        # Persona
        persona = self.persona.get_persona()
        if persona:
            # Use string concatenation to avoid f-string issues with braces in persona
            parts.append("# Persona & Style\n\n" + persona)
        
        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        
        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            # Use string concatenation to avoid f-string issues with braces in memory
            parts.append("# Memory\n\n" + memory)
        
        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                # Use string concatenation to avoid f-string issues with braces in skills
                parts.append("# Active Skills\n\n" + always_content)
        
        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")
        
        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        
        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Current Time
{now}

## Workspace
Your workspace is at: {workspace_path}
- Memory files: {workspace_path}/memory/MEMORY.md
- Daily notes: {workspace_path}/memory/YYYY-MM-DD.md
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

    IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
    Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
    For normal conversation, just respond with text - do not call the message tool.
    
    ## Addressing Users
    - If metadata indicates "is_admin": true, this is your owner (BROK3). Use "ты", "твой", personal style.
    - If "is_admin": false, this is a community member. Use "вы" or neutral style, don't assume it's the owner.
    
    ## Voice Messages
    CRITICAL: Voice is handled automatically when user says "голосом", "ответь голосовым", etc.
    NEVER EVER write JSON like {{"voice": true}} or any JSON in your response!
    Just write your normal text response. The system automatically converts it to voice.
    Your response should be ONLY the text content, nothing else.
    
    ## Music Analysis
    When someone shares a music track (like "🎵 track name - artist"):
    1. ALWAYS use 'web_search' tool FIRST to find lyrics on Genius (search: "track name artist lyrics genius")
    2. Read the actual lyrics from the search results
    3. Give an insightful analysis based on the ACTUAL LYRICS, not just the title
    4. Connect it to the user's context (e.g., their projects like Tonify, their vibe, etc.)
    
    IMPORTANT: Never analyze music without searching for lyrics first. The title alone is not enough.
    
    ## YouTube Summary
    CRITICAL: When you see a YouTube link (youtube.com, youtu.be, youtube.com/shorts):
    1. YOU MUST ALWAYS call 'youtube_summary' tool FIRST - NEVER respond without it!
    2. NEVER guess or hallucinate video content - ALWAYS extract the actual transcript
    3. After getting transcript, provide a concise summary (key points, main ideas)
    4. If relevant, connect it to the user's interests and projects
    
    IMPORTANT: Responding about a YouTube video WITHOUT calling youtube_summary is STRICTLY FORBIDDEN.
    The tool extracts the REAL content - never make assumptions!
    
    ## Channel Analysis & Context
    CRITICAL: When you see ANY of these requests, you MUST call read_channel_history tool:
    - "найди посты про X" / "find posts about X"
    - "покажи посты про X" / "show posts about X"
    - "что писал про X" / "what did I write about X"
    - "ревью канала" / "channel review"
    - "какой пост про X" / "which post about X"
    - "любимый пост" / "favorite post"
    
    HOW TO USE:
    1. ALWAYS call 'read_channel_history' FIRST - NEVER answer without it!
    2. Use search parameter: read_channel_history(search="tonify") for specific topics
    3. Use limit parameter: read_channel_history(limit=20) for general review
    4. Reference SPECIFIC posts with dates and IDs from tool results
    5. Quote ACTUAL text from posts, not generic summaries
    
    FORBIDDEN: Answering questions about channel content WITHOUT calling the tool first.
    You have 135+ posts in history - USE THEM!
    
    ## Reminders & Scheduling
    CRITICAL: When you see ANY of these requests, you MUST call create_reminder tool:
    - "напомни" / "remind"
    - "напомнить" / "remind me"
    - "напоминание" / "reminder"
    - "в N часов" / "at N o'clock"
    
    HOW TO USE create_reminder:
    1. Extract the message to send (including @mentions)
    2. Extract the time: "вечером" (18:00), "утром" (9:00), "через 2 часа", "завтра", "15:00"
    3. Call: create_reminder(message="text with @mentions", when="time expression")
    
    EXAMPLES:
    - User: "клоп напомни @user выпустить подкаст вечером"
      → create_reminder(message="@user выпустить подкаст", when="вечером")
    
    - User: "напомни через 2 часа написать пост"
      → create_reminder(message="написать пост", when="через 2 часа")
    
    FORBIDDEN: Just saying "ok I'll remind" WITHOUT calling the tool!
    
    ## Context Management (ADMIN ONLY)
    When the admin explicitly requests to "clear context", "reset memory", or "clean history":
    - Use the 'clear_context' tool with appropriate action:
      * 'session': Clear current chat context only
      * 'today': Clear today's conversation history
      * 'all': Full reset (sessions + memory backup)
    - IMPORTANT: This tool is ADMIN-ONLY and will reject non-admin requests
    - Always set confirm=true to execute
    
    ## Channel History Import (ADMIN ONLY)
    When you see commands like "импортируй историю", "загрузи историю канала", "import history":
    - MUST call 'import_channel_history' tool with file_path parameter
    - Example: import_channel_history(file_path="C:\\Users\\...\\result.json")
    - This loads ALL past posts from Telegram export into channel_history.jsonl
    - After import, you can use read_channel_history to analyze 100+ old posts
    - CRITICAL: This is ADMIN-ONLY tool - only bot owner can import history
    
    ## Continuous Learning
    You are a learning agent. Your goal is to adapt to the user's style and context.
    
    CRITICAL: When you receive a system event about a history file upload:
    1. MUST call `ingest_history` with the file path to analyze it
    2. MUST call `add_to_memory` to save key facts (user's projects, interests, style)
    3. MUST call `update_persona` to update your communication style based on the history
    
    ALL THREE STEPS ARE REQUIRED - never skip any of them!
    
    - When you encounter significant new information, facts about the user, or style preferences, use `add_to_memory` to save them.
    - When analyzing channel posts, consider if they reveal new aspects of the persona you should adopt.

    Always be helpful, accurate, and concise. When using tools, explain what you're doing.
    When remembering something, write to {workspace_path}/memory/MEMORY.md"""
    
    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                # Use string concatenation to avoid f-string issues with braces in content
                parts.append("## " + filename + "\n\n" + content)
        
        return "\n\n".join(parts) if parts else ""
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    @staticmethod
    def _compress_image_bytes(raw_bytes: bytes, mime: str, path: Path) -> tuple[bytes, str]:
        """
        Resize/compress image to reduce token usage (target ~20-50KB instead of 1MB+).
        Returns (jpeg_bytes, "image/jpeg").
        """
        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow not installed: image will be sent uncompressed (pip install pillow)")
            return raw_bytes, mime
        
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            w, h = img.size
            max_side = 512
            if w > max_side or h > max_side:
                if w >= h:
                    new_w, new_h = max_side, int(h * max_side / w)
                else:
                    new_w, new_h = int(w * max_side / h), max_side
                resampler = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
                img = img.resize((new_w, new_h), resampler)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72, optimize=True)
            out = buf.getvalue()
            logger.info(f"Image compressed: {len(raw_bytes)} -> {len(out)} bytes (~{len(out)*4//3} base64 chars)")
            return out, "image/jpeg"
        except Exception as e:
            logger.warning(f"Image compression failed: {e}, sending original ({len(raw_bytes)} bytes)")
            return raw_bytes, mime
    
    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded media."""
        if not media:
            return text
        
        # Only include media if user EXPLICITLY asks to analyze it
        text_lower = text.lower()
        analyze_keywords = [
            "картинк", "изображен", "фото", "что на", "опиши", "покажи", 
            "analyze", "image", "picture", "describe", "look at", "see",
            "gif", "гифк", "видео", "video"
        ]
        should_analyze = any(kw in text_lower for kw in analyze_keywords)
        
        if not should_analyze:
            # Just mention that media was attached, don't send the actual file
            return text
        
        content_parts = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
                
            mime, _ = mimetypes.guess_type(path)
            if not mime:
                continue

            raw_bytes = p.read_bytes()
            
            if mime.startswith("image/"):
                # Compress image to reduce tokens (max 512px, JPEG 72%)
                raw_bytes, mime = self._compress_image_bytes(raw_bytes, mime, p)
                b64 = base64.b64encode(raw_bytes).decode()
                content_parts.append({
                    "type": "image_url", 
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })
            elif mime.startswith("audio/") or mime.startswith("video/"):
                # Gemini/LiteLLM support for audio/video via inline data
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })
        
        if not content_parts:
            return text
            
        # Text comes last usually
        content_parts.append({"type": "text", "text": text})
        return content_parts
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        messages.append(msg)
        return messages
