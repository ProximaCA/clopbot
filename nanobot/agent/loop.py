"""Agent loop: the core processing engine."""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.ingest import IngestHistoryTool
from nanobot.agent.tools.memory_tools import AddToMemoryTool, UpdatePersonaTool
from nanobot.agent.tools.youtube import YouTubeSummaryTool
from nanobot.agent.tools.context_management import ClearContextTool
from nanobot.agent.tools.channel_history import ReadChannelHistoryTool
from nanobot.agent.tools.import_channel_history import ImportChannelHistoryTool
from nanobot.agent.tools.reminder import ReminderTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        admin_ids: list[str] | None = None,
        cron_service=None
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.admin_ids = admin_ids or []
        self.cron_service = cron_service
        
        self.context = ContextBuilder(workspace)
        self.sessions = SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
        )
        
        self._running = False
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools
        self.tools.register(ReadFileTool())
        self.tools.register(WriteFileTool())
        self.tools.register(EditFileTool())
        self.tools.register(ListDirTool())
        
        # Shell tool
        self.tools.register(ExecTool(working_dir=str(self.workspace)))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(YouTubeSummaryTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Ingestion & Memory tools
        self.tools.register(IngestHistoryTool())
        self.tools.register(AddToMemoryTool(self.workspace))
        self.tools.register(UpdatePersonaTool(self.workspace))
        
        # Channel history tools
        self.tools.register(ReadChannelHistoryTool(self.workspace))
        self.tools.register(ImportChannelHistoryTool(
            workspace=self.workspace,
            admin_ids=self.admin_ids
        ))
        
        # Context management tool (admin-only)
        self.tools.register(ClearContextTool(
            workspace=self.workspace,
            admin_ids=self.admin_ids,
            session_manager=self.sessions
        ))
        
        # Music analysis tool
        from nanobot.agent.tools.music import MusicAnalysisTool
        self.tools.register(MusicAnalysisTool())
        
        # Reminder tool (with cron service)
        if self.cron_service:
            self.tools.register(ReminderTool(
                workspace=self.workspace,
                cron_service=self.cron_service
            ))
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                logger.info(f"Received message from bus: {msg.channel}:{msg.sender_id}")
                try:
                    response = await self._process_message(msg)
                    logger.info(f"Message processing complete, response: {bool(response)}")
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    import traceback
                    logger.error(f"Error processing message: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        logger.info(f"_process_message started for {msg.channel}:{msg.sender_id}")
        logger.debug(f"Message content preview: {msg.content[:100]}...")
        
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            logger.info("Detected system message, routing to _process_system_message")
            return await self._process_system_message(msg)
        
        # Check if this is a channel post - add context for public comment
        if msg.metadata.get("is_channel_post"):
            channel_title = msg.metadata.get("channel_title", "Unknown")
            original_content = msg.content
            # Use string concatenation to avoid f-string issues with braces in content
            msg.content = (
                "SYSTEM: You are commenting on a channel post in '" + channel_title + "'. "
                "This is a PUBLIC comment visible to all channel subscribers. "
                "Respond directly to the post as a community member, not as an analyst reporting to someone.\n\n"
                "POST CONTENT:\n" + original_content + "\n\n"
                "Write your public comment below (keep it concise and engaging):"
            )
            logger.info(f"Processing CHANNEL POST from {channel_title}")
        
        logger.info(f"Processing regular message from {msg.channel}:{msg.sender_id}")
        
        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        # Add context about who is messaging (admin vs regular user)
        current_message = msg.content
        if msg.metadata.get("is_group"):
            if msg.metadata.get("is_admin"):
                current_message = "[Admin/Owner] " + msg.content
            else:
                username = msg.metadata.get("username") or msg.metadata.get("first_name") or "User"
                current_message = f"[Community member: {username}] " + msg.content
        
        # Auto-detect and extract YouTube transcripts BEFORE LLM call
        import re
        youtube_pattern = r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/|youtube\.com\/embed\/)([^\s&\n?#]+)'
        youtube_match = re.search(youtube_pattern, msg.content)
        
        if youtube_match:
            youtube_url = youtube_match.group(0)
            logger.info(f"YouTube link detected: {youtube_url}. Auto-extracting transcript...")
            
            # Execute youtube_summary tool automatically
            youtube_tool = self.tools.get("youtube_summary")
            if youtube_tool:
                try:
                    transcript_result = await youtube_tool.execute(url=youtube_url)
                    logger.info(f"YouTube transcript extracted: {len(transcript_result)} chars")
                    
                    # Prepend transcript to user message so LLM has context
                    current_message = current_message + "\n\n[SYSTEM: Auto-extracted transcript below]\n" + transcript_result
                except Exception as e:
                    logger.error(f"Failed to auto-extract YouTube transcript: {e}")
                    current_message = current_message + f"\n\n[SYSTEM: Failed to extract YouTube transcript: {e}]"
            else:
                logger.warning("youtube_summary tool not found in registry")
        
        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=current_message,
            media=msg.media if msg.media else None,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Agent loop iteration {iteration}/{self.max_iterations}")
            
            # Call LLM with timeout
            logger.info("Calling LLM...")
            try:
                response = await asyncio.wait_for(
                    self.provider.chat(
                        messages=messages,
                        tools=self.tools.get_definitions(),
                        model=self.model
                    ),
                    timeout=120.0  # Increased timeout for slow models/network
                )
                logger.info(f"LLM responded. Has tool calls: {response.has_tool_calls}")
            except asyncio.TimeoutError:
                logger.error("LLM call timed out after 120 seconds!")
                final_content = "⏳ Бро, я немного задумался (таймаут LLM). Спроси еще раз через пару минут, я перезагрузил нейронку."
                break
            
            # Handle tool calls
            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                # Execute tools
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.info(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    
                    # Set user context for admin-only tools
                    if tool_call.name in ["clear_context", "import_channel_history"]:
                        tool = self.tools.get(tool_call.name)
                        if tool and hasattr(tool, 'set_user_context'):
                            user_id = str(msg.metadata.get("user_id", "unknown"))
                            is_admin = msg.metadata.get("is_admin", False)
                            tool.set_user_context(user_id, is_admin)
                    
                    # Set chat context for channel history and reminder tools (for chat isolation)
                    if tool_call.name in ["read_channel_history", "import_channel_history", "create_reminder"]:
                        tool = self.tools.get(tool_call.name)
                        if tool and hasattr(tool, 'set_chat_context'):
                            # Get chat_id from metadata (from Telegram chat ID)
                            chat_id = msg.metadata.get("chat_id", msg.sender_id)
                            tool.set_chat_context(str(chat_id))
                    
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        logger.info(f"Tool {tool_call.name} completed successfully.")
                    except Exception as e:
                        logger.error(f"Tool {tool_call.name} failed: {e}")
                        result = f"Error executing tool: {str(e)}"
                        
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # No tool calls, we're done
                final_content = response.content
                logger.info("Agent loop finished (no more tool calls).")
                break
        
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        
        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        # Check if user requested voice response
        user_msg_lower = msg.content.lower()
        use_voice = any(phrase in user_msg_lower for phrase in [
            "ответь голосовым",
            "ответит голосовым",
            "ответить голосовым",
            "отправь голосовым",
            "голосом ответь",
            " голосом",  # "расскажи голосом", "напиши голосом"
            " голосовым",  # "поприветсвуй голосовым", "сделай голосовым"
            "голосом.",  # "голосом."
            "голосовым.",  # "голосовым."
            "send voice",
            "voice message",
            "голосовое сообщение"
        ])
        
        metadata = {}
        if use_voice:
            metadata["voice"] = True
            logger.info("Voice response requested by user")
        
        # Pass through inline request ID for inline mode responses
        if msg.metadata.get("inline_request_id"):
            metadata["inline_request_id"] = msg.metadata["inline_request_id"]
            
        # Default to replying to the incoming message if message_id is available
        reply_to_id = msg.metadata.get("message_id")
        
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            reply_to=reply_to_id,
            metadata=metadata
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"System message loop iteration {iteration}/{self.max_iterations}")
            
            logger.info("Calling LLM for system message...")
            try:
                response = await asyncio.wait_for(
                    self.provider.chat(
                        messages=messages,
                        tools=self.tools.get_definitions(),
                        model=self.model
                    ),
                    timeout=60.0  # 60 second timeout
                )
                logger.info(f"LLM responded. Has tool calls: {response.has_tool_calls}")
            except asyncio.TimeoutError:
                logger.error("LLM call timed out after 60 seconds!")
                final_content = "Sorry, the request took too long to process."
                break
            
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.info(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        logger.info(f"Tool {tool_call.name} completed successfully.")
                    except Exception as e:
                        logger.error(f"Tool {tool_call.name} failed: {e}")
                        result = f"Error executing tool: {str(e)}"
                        
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                logger.info("System message loop finished (no more tool calls).")
                break
        
        if final_content is None:
            final_content = "Background task completed."
        
        # Save to session (mark as system message in history)
        # Use string concatenation to avoid f-string issues with braces in content
        session.add_message("user", "[System: " + msg.sender_id + "] " + msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(self, content: str, session_key: str = "cli:direct") -> str:
        """
        Process a message directly (for CLI usage).
        
        Args:
            content: The message content.
            session_key: Session identifier.
        
        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content=content
        )
        
        response = await self._process_message(msg)
        return response.content if response else ""
