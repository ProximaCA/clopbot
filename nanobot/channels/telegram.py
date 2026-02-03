"""Telegram channel implementation using python-telegram-bot."""

import asyncio
import re
import os
import tempfile

from loguru import logger
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, MessageHandler, filters, ContextTypes, InlineQueryHandler

try:
    import edge_tts
except ImportError:
    edge_tts = None

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import TelegramConfig


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.
    """
    if not text:
        return ""
    
    # 1. Extract and protect code blocks (preserve content from other processing)
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)
    
    # 2. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    
    text = re.sub(r'`([^`]+)`', save_inline_code, text)
    
    # 3. Headers # Title -> just the title text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    
    # 4. Blockquotes > text -> just the text (before HTML escaping)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    
    # 5. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # 6. Links [text](url) - must be before bold/italic to handle nested cases
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    
    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    
    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)
    
    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    
    # 10. Bullet lists - item -> • item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    # 11. Restore inline code with HTML tags
    for i, code in enumerate(inline_codes):
        # Escape HTML in code content
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")
    
    # 12. Restore code blocks with HTML tags
    for i, code in enumerate(code_blocks):
        # Escape HTML in code content
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")
    
    return text


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling.
    
    Simple and reliable - no webhook/public IP needed.
    """
    
    name = "telegram"
    
    def __init__(self, config: TelegramConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self._app: Application | None = None
        self._chat_ids: dict[str, int] = {}  # Map sender_id to chat_id for replies
        self._user_states: dict[str, str] = {}  # Track user states (e.g. 'waiting_for_history')
        
        # Trigger keywords for group chat
        self.direct_triggers = ["клоп", "бот", "клопбот", "clopbot", "бро"]  # Direct mentions
        self.context_triggers = ["tonify", "ton", "nigredo", "web3", "degen", "spotify", "soundcloud"]  # Context triggers for random responses
    
    async def start(self) -> None:
        """Start the Telegram bot with long polling."""
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return
        
        self._running = True
        
        # Build the application
        self._app = (
            Application.builder()
            .token(self.config.token)
            .build()
        )
        
        # Add message handler for text, photos, voice, documents
        # Note: In group chats, bots with Privacy Mode ON only receive:
        # - Commands (/)
        # - Messages that mention the bot (@bot_username)
        # - Messages if the bot is an admin with Privacy Mode OFF
        # To receive all messages in groups, disable Privacy Mode in @BotFather
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL) 
                & ~filters.COMMAND & ~filters.ChatType.CHANNEL, 
                self._on_message
            )
        )

        # Channel post handler DISABLED - we use auto-forwarded posts in discussion group instead
        # This prevents the bot from posting directly to the channel
        # self._app.add_handler(
        #     MessageHandler(
        #         filters.ChatType.CHANNEL & (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL),
        #         self._on_channel_post
        #     )
        # )
        
        # Add /start command handler
        from telegram.ext import CommandHandler
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("init", self._on_init))
        
        # Add inline query handler (admin-only)
        self._app.add_handler(InlineQueryHandler(self._on_inline_query))
        
        logger.info("Starting Telegram bot (polling mode)...")
        
        # Initialize and start polling
        await self._app.initialize()
        await self._app.start()
        
        # Get bot info
        bot_info = await self._app.bot.get_me()
        logger.info(f"Telegram bot @{bot_info.username} connected")
        
        # Start polling (this runs until stopped)
        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True  # Ignore old messages on startup
        )
        
        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False
        
        if self._app:
            logger.info("Stopping Telegram bot...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None
    
    def _should_respond_in_group(self, text: str, has_mention: bool) -> tuple[bool, str]:
        """
        Check if bot should respond to a group message.
        
        Returns:
            (should_respond, reason)
        """
        import random
        import re
        
        if not text:
            return False, "no_text"
        
        text_lower = text.lower()
        
        # Always respond to direct @mention
        if has_mention:
            return True, "direct_mention"
        
        # Check for direct trigger keywords as WHOLE WORDS (not substrings)
        # Using word boundary \b to avoid matching "работать" for "бот" or "клоповник" for "клоп"
        for trigger in self.direct_triggers:
            # Pattern: word boundary + trigger + word boundary
            pattern = r'\b' + re.escape(trigger) + r'\b'
            if re.search(pattern, text_lower):
                return True, f"trigger_{trigger}"
        
        # Check for context triggers with probability (also as whole words)
        for trigger in self.context_triggers:
            pattern = r'\b' + re.escape(trigger) + r'\b'
            if re.search(pattern, text_lower):
                # 20% chance to respond to context triggers
                if random.random() < 0.2:
                    return True, f"random_context_{trigger}"
        
        return False, "no_trigger"
    
    async def _save_channel_post(self, post_id: int, content: str, date: str, from_user: str, chat_id: int) -> None:
        """Save channel post to chat-specific history file for context."""
        try:
            from pathlib import Path
            import json
            
            # Get workspace from config
            workspace = Path.home() / ".nanobot" / "workspace"
            histories_dir = workspace / "channel_histories"
            histories_dir.mkdir(exist_ok=True)
            
            # Create chat-specific history file
            safe_chat_id = str(chat_id).replace("-", "neg").replace("|", "_")
            history_file = histories_dir / f"chat_{safe_chat_id}.jsonl"
            
            # Create entry
            entry = {
                "id": post_id,
                "date": date,
                "from": from_user,
                "content": content,
                "timestamp": __import__('datetime').datetime.now().isoformat()
            }
            
            # Append to JSONL file
            with open(history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            
            logger.debug(f"Saved post #{post_id} to channel history")
            
        except Exception as e:
            logger.error(f"Failed to save channel post to history: {e}")
    
    async def _generate_voice(self, text: str) -> str | None:
        """Generate voice from text using edge-tts."""
        if not edge_tts:
            logger.warning("edge-tts not installed, skipping voice generation")
            return None
            
        try:
            # Clean text for TTS (remove markdown, emojis, special chars)
            clean_text = re.sub(r'[*_`]', '', text)  # Remove markdown
            clean_text = re.sub(r'[^\w\s\.,!?\-]', '', clean_text)  # Remove emojis and special chars
            clean_text = clean_text.strip()
            
            # Use Russian male voice with 1.2x speed
            voice = "ru-RU-DmitryNeural"
            communicate = edge_tts.Communicate(clean_text, voice, rate="+20%")
            
            # Create temp file
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            
            await communicate.save(path)
            return path
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
            return None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Telegram."""
        if not self._app:
            logger.warning("Telegram bot not running")
            return
        
        try:
            # chat_id should be the Telegram chat ID (integer)
            chat_id = int(msg.chat_id)
            
            # Prepare reply parameters
            reply_kwargs = {}
            if msg.reply_to:
                reply_kwargs["reply_to_message_id"] = int(msg.reply_to)
            
            # Check for voice generation
            # Only use voice if explicitly requested via metadata
            should_use_voice = msg.metadata.get("voice", False) and edge_tts
            
            if should_use_voice:
                voice_path = await self._generate_voice(msg.content)
                if voice_path:
                    try:
                        # Send voice note WITHOUT caption (voice only)
                        with open(voice_path, "rb") as f:
                            await self._app.bot.send_voice(
                                chat_id=chat_id,
                                voice=f,
                                **reply_kwargs
                            )
                        logger.info("Voice message sent successfully")
                        # Clean up
                        os.unlink(voice_path)
                        return
                    except Exception as e:
                        logger.error(f"Failed to send voice: {e}")
                        # Fallback to text if voice fails

            # Convert markdown to Telegram HTML
            html_content = _markdown_to_telegram_html(msg.content)
            
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=html_content,
                    parse_mode="HTML",
                    **reply_kwargs
                )
                logger.info(f"Message sent to {chat_id}")
            except Exception as html_error:
                # Only fallback to plain text if HTML parsing specifically failed
                error_str = str(html_error).lower()
                if "can't parse" in error_str or "parse error" in error_str or "invalid" in error_str:
                    logger.warning(f"HTML parse failed, falling back to plain text: {html_error}")
                    try:
                        await self._app.bot.send_message(
                            chat_id=chat_id,
                            text=msg.content,
                            **reply_kwargs
                        )
                    except Exception as e2:
                        logger.error(f"Error sending plain text message: {e2}")
                else:
                    # Other errors (SSL, network, etc.) - don't retry, just log
                    logger.error(f"Error sending Telegram message: {html_error}")
        except ValueError:
            logger.error(f"Invalid chat_id: {msg.chat_id}")
    
    async def react(self, chat_id: str | int, message_id: int, emoji: str = "👀") -> bool:
        """
        Set a reaction on a message.
        
        Args:
            chat_id: Chat ID where the message is
            message_id: Message ID to react to
            emoji: Emoji to react with (default: 👀)
        
        Returns:
            True if reaction was set successfully
        """
        if not self._app:
            return False
        
        try:
            from telegram import ReactionTypeEmoji
            
            await self._app.bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
            logger.debug(f"Set reaction {emoji} on message {message_id} in {chat_id}")
            return True
        except Exception as e:
            # Reactions might not be available in all chats or for all bots
            logger.debug(f"Could not set reaction: {e}")
            return False
    
    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return
        
        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm nanobot.\n\n"
            "Send me a message and I'll respond!\n"
            "Use /init to upload channel history."
        )

    async def _on_init(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /init command."""
        if not update.message or not update.effective_user:
            return
            
        user_id = str(update.effective_user.id)
        self._user_states[user_id] = "waiting_for_history"
        
        await update.message.reply_text(
            "📂 Please upload the channel history JSON file.\n"
            "I'll ingest it to learn your style and context."
        )
    
    async def _on_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline queries (@clopbot_bot query) - admin only."""
        logger.info(f">>> INLINE QUERY HANDLER CALLED <<<")
        
        if not update.inline_query:
            logger.warning("No inline_query in update")
            return
        
        query = update.inline_query.query
        logger.info(f"INLINE QUERY: '{query}' from user {update.inline_query.from_user.id}")
        user = update.inline_query.from_user
        user_id = str(user.id)
        
        logger.info(f"Inline query from {user.username or user_id}: {query[:50]}...")
        
        # Admin-only check
        allow_list = getattr(self.config, "allow_from", [])
        is_admin = allow_list and user_id in allow_list
        
        if not is_admin:
            # Non-admin: show "access denied" result
            results = [
                InlineQueryResultArticle(
                    id="access_denied",
                    title="⛔ Access Denied",
                    description="Inline mode is only available for the bot owner.",
                    input_message_content=InputTextMessageContent(
                        message_text="⛔ Inline mode is admin-only."
                    )
                )
            ]
            await update.inline_query.answer(results, cache_time=60)
            return
        
        if not query or len(query.strip()) < 2:
            # Empty or too short query: show help
            results = [
                InlineQueryResultArticle(
                    id="help",
                    title="🐛 clopbot inline",
                    description="Type your question to get an answer",
                    input_message_content=InputTextMessageContent(
                        message_text="🐛 Use: @clopbot_bot your question"
                    )
                )
            ]
            await update.inline_query.answer(results, cache_time=10)
            return
        
        # Call LLM directly for quick inline response
        logger.info(f"Processing inline query: {query[:30]}...")
        try:
            import hashlib
            import json
            from nanobot.providers.litellm_provider import LiteLLMProvider
            from nanobot.config.loader import load_config
            from nanobot.agent.tools.web import WebSearchTool
            
            # Create stable ID from query
            query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
            display_query = query[:50] + "..." if len(query) > 50 else query
            
            # Quick LLM call
            try:
                logger.info("Loading config for inline LLM...")
                config = load_config()
                
                # Check API keys
                api_key = config.get_api_key()
                brave_key = config.tools.web.search.api_key or os.environ.get("BRAVE_API_KEY")
                
                # Use fastest model for inline
                # If using Gemini, switch to Flash for speed
                model = config.agents.defaults.model
                if "gemini" in model.lower():
                    # User requested specific model alias
                    model = "gemini/gemini-flash-lite-latest"
                    os.environ["GEMINI_API_KEY"] = api_key
                elif api_key:
                     # Setup other env vars
                     if "claude" in model.lower(): os.environ["ANTHROPIC_API_KEY"] = api_key
                     if "gpt" in model.lower(): os.environ["OPENAI_API_KEY"] = api_key

                provider = LiteLLMProvider(
                    api_key=api_key,
                    default_model=model
                )
                
                context_info = ""
                
                # STRATEGY 1: Brave Search (Fastest & Best)
                if brave_key:
                    logger.info("Using Brave Search for speed...")
                    try:
                        search_tool = WebSearchTool(api_key=brave_key, max_results=2)
                        # Execute search manually BEFORE calling LLM (saves 1 roundtrip)
                        search_results = await asyncio.wait_for(
                            search_tool.execute(query), 
                            timeout=3.0
                        )
                        context_info = f"SEARCH RESULTS:\n{search_results}\n\n"
                        logger.info("Brave search successful")
                    except Exception as e:
                        logger.error(f"Brave search failed: {e}")
                        context_info = ""
                
                # STRATEGY 2: Google Native (Fallback)
                # Use if (No Brave OR Brave failed) AND using Gemini
                tools = None
                answer = None
                
                # If we don't have context from Brave, try Google Native
                if not context_info and "gemini" in model.lower():
                    logger.info("Using Gemini Native Search (via google.genai) as fallback...")
                    try:
                        from google import genai
                        from google.genai import types
                        
                        client = genai.Client(api_key=api_key)
                        # Use stable Flash model for native search tools
                        native_model = "gemini-2.5-flash-lite" 
                        
                        logger.info(f"Calling native GenerateContent ({native_model})...")
                        
                        # Run sync call in thread
                        def _native_generate():
                            # Enforce Russian and brevity in prompt
                            enhanced_query = (
                                f"Ответь на русском языке. Будь краток (макс 300 символов). "
                                f"Используй Google Search чтобы найти актуальную информацию. "
                                f"Вопрос пользователя: {query}"
                            )
                            
                            response = client.models.generate_content(
                                model=native_model,
                                contents=enhanced_query,
                                config=types.GenerateContentConfig(
                                    tools=[types.Tool(google_search=types.GoogleSearch())],
                                    max_output_tokens=300
                                )
                            )
                            return response.text

                        answer = await asyncio.to_thread(_native_generate)
                        logger.info(f"Native answer: {answer[:50]}...")
                        
                    except ImportError:
                         logger.warning("google-genai not installed. Skipping native search.")
                    except Exception as e:
                         logger.error(f"Native search failed: {e}")
                
                # If answer not obtained via native search, use standard LLM (no tools to avoid errors)
                if not answer:
                    # Construct Prompt
                    system_prompt = (
                        "You are clopbot. Answer the user query concisely (max 250 chars). "
                        f"{'Using provided search results.' if context_info else 'Answer from knowledge.'}"
                    )
                    
                    user_content = f"{context_info}User Query: {query}"
                    
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ]
                    
                    logger.info(f"Calling LLM (Standard)...")
                    
                    # Single LLM Call (Strict 9s timeout)
                    response = await asyncio.wait_for(
                        provider.chat(messages, max_tokens=250, max_retries=0),
                        timeout=9.0
                    )
                    answer = response.content if hasattr(response, "content") else str(response)

                logger.info(f"LLM final answer: {str(answer)[:100]}...")
                
                if not answer or "Error calling LLM" in answer:
                     answer = "⚠️ LLM Error. Try again."
                
                if len(answer) > 500:
                    answer = answer[:500] + "..."
                    
            except asyncio.TimeoutError:
                logger.warning("Inline LLM timeout!")
                answer = "⏱ Timeout. Add Brave API key for speed."
            except Exception as llm_error:
                logger.error(f"Inline LLM error: {llm_error}")
                answer = f"🐛 Error: {str(llm_error)[:50]}"
            
            logger.info(f"Preparing inline result with answer: {answer[:50]}...")
            results = [
                InlineQueryResultArticle(
                    id=f"ans_{query_hash}",
                    title=f"🐛 {display_query}",
                    description=answer[:100] + "..." if len(answer) > 100 else answer,
                    input_message_content=InputTextMessageContent(
                        message_text=f"❓ {query}\n\n🐛 {answer}"
                    )
                )
            ]
            
            logger.info("Sending inline answer to Telegram...")
            try:
                await update.inline_query.answer(results, cache_time=60, is_personal=True)
                logger.info(f"Inline answered successfully: {display_query}")
            except Exception as tg_error:
                if "Query is too old" in str(tg_error):
                    logger.warning(f"Telegram query timed out (too old): {display_query}")
                else:
                    raise tg_error
            
        except Exception as e:
            logger.error(f"Inline query error: {e}")
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages (text, photos, voice, documents)."""
        if not update.message or not update.effective_user:
            return
        
        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        
        # Log incoming message details
        chat_type = message.chat.type
        
        # Check if this is a channel post forwarded to discussion group
        is_channel_post = False
        # Distinguish between:
        # 1. Real channel post (auto-forwarded from channel to discussion group)
        # 2. User writing in chat "from channel identity"
        is_auto_forward = getattr(message, 'is_automatic_forward', False)
        
        if message.sender_chat:
            if is_auto_forward:
                # Real channel post auto-forwarded to discussion
                is_channel_post = True
                logger.info(f"Received REAL CHANNEL POST (auto-forward) in chat_id={chat_id}, from_channel={message.sender_chat.title or message.sender_chat.id}")
            else:
                # User writing from channel identity in chat - treat as regular message
                is_channel_post = False
                logger.info(f"Received message FROM CHANNEL IDENTITY in chat_id={chat_id}, channel={message.sender_chat.title or message.sender_chat.id}")
        else:
            logger.info(f"Received message in chat_type={chat_type}, chat_id={chat_id}, from={user.username or user.id}")
        
        # Use stable numeric ID, but keep username for allowlist compatibility
        sender_id = str(user.id)
        if user.username:
            sender_id = f"{sender_id}|{user.username}"
        
        # Store chat_id for replies
        self._chat_ids[sender_id] = chat_id

        # Check if this is a reply to bot - prioritize over other checks
        is_reply_to_bot = False
        if message.reply_to_message:
            if message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
                is_reply_to_bot = True
                logger.debug("Message is a reply to bot - skipping state check")

        # Check for history upload state (but skip if replying to bot)
        user_id_str = str(user.id)
        if not is_reply_to_bot and self._user_states.get(user_id_str) == "waiting_for_history":
            if message.document:
                try:
                    file = await self._app.bot.get_file(message.document.file_id)
                    
                    # Save to workspace/data/history.json
                    from pathlib import Path
                    data_dir = Path.home() / ".nanobot" / "data"
                    data_dir.mkdir(parents=True, exist_ok=True)
                    file_path = data_dir / "history.json"
                    
                    await file.download_to_drive(str(file_path))
                    
                    # Reset state
                    del self._user_states[user_id_str]
                    
                    # Notify user
                    await message.reply_text("✅ History file received. Processing...")
                    
                    # Trigger agent ingestion (skip allow check for system events)
                    await self._handle_message(
                        sender_id=sender_id,
                        chat_id=str(chat_id),
                        content=f"SYSTEM: History file uploaded to {file_path}. Please use the 'ingest_history' tool to process it and update my persona.",
                        media=[],
                        metadata={"type": "system_event"},
                        skip_allow_check=True
                    )
                    return
                except Exception as e:
                    logger.error(f"Failed to download history: {e}")
                    await message.reply_text("❌ Failed to download file. Please try again.")
                    return
            else:
                # If not a document, user sent something else
                # Reset state and process the message normally
                logger.info(f"User {user_id_str} was waiting for history but sent regular message. Resetting state.")
                del self._user_states[user_id_str]
                # Continue to process the message below
        
        # Build content from text and/or media
        content_parts = []
        media_paths = []
        
        # Text content
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)
        
        # Handle media files
        media_file = None
        media_type = None
        
        if message.photo:
            media_file = message.photo[-1]  # Largest photo
            media_type = "image"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.audio:
            media_file = message.audio
            media_type = "audio"
        elif message.document:
            media_file = message.document
            media_type = "file"
        
        # Download media if present
        if media_file and self._app:
            try:
                file = await self._app.bot.get_file(media_file.file_id)
                ext = self._get_extension(media_type, getattr(media_file, 'mime_type', None))
                
                # Save to workspace/media/
                from pathlib import Path
                media_dir = Path.home() / ".nanobot" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                
                file_path = media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))
                
                media_paths.append(str(file_path))
                content_parts.append(f"[{media_type}: {file_path}]")
                logger.debug(f"Downloaded {media_type} to {file_path}")
            except Exception as e:
                logger.error(f"Failed to download media: {e}")
                content_parts.append(f"[{media_type}: download failed]")
        
        content = "\n".join(content_parts) if content_parts else "[empty message]"
        
        logger.debug(f"Telegram message from {sender_id}: {content[:50]}...")
        
        # In group chats, allow everyone. In private chats, check allowFrom.
        is_group = message.chat.type in ("group", "supergroup")
        
        # Whitelist for group/channel chats: only respond in allowed chat IDs
        allow_chats = getattr(self.config, "allow_chats", []) or []
        if allow_chats and (is_group or is_channel_post):
            chat_id_str = str(chat_id)
            try:
                chat_id_abs = str(abs(int(chat_id)))
            except (ValueError, TypeError):
                chat_id_abs = chat_id_str
            if chat_id_str not in allow_chats and chat_id_abs not in allow_chats:
                logger.info(f"Ignoring message from non-whitelisted chat: {chat_id} (add to allow_chats in config)")
                return
        
        # In group chats, check if bot should respond based on triggers
        if is_group:
            # Filter out meaningless replies (emoji-only, too short, etc.)
            # Remove emojis and whitespace to check if there's actual text
            import re
            text_only = re.sub(r'[^\w\s]', '', content)  # Remove punctuation/emoji
            text_only = text_only.strip()
            
            is_meaningful = len(text_only) >= 3  # At least 3 characters of text
            
            # Check if sender is admin
            is_admin = False
            allow_list = getattr(self.config, "allow_from", [])
            if allow_list and str(user.id) in allow_list:
                is_admin = True
            
            # Handle replies to bot
            if is_reply_to_bot:
                if not is_meaningful:
                    logger.debug(f"Ignoring meaningless reply (emoji-only or too short): {content[:20]}")
                    return
                
                # ALWAYS respond to replies (admin or regular user)
                logger.info(f"Responding to group message: reply_to_bot ({'admin' if is_admin else 'user'})")
            # For REAL channel posts (auto-forwarded), ALWAYS respond (feedback)
            elif is_channel_post:
                logger.info("Responding to REAL CHANNEL POST (auto-forward) - providing feedback")
            else:
                # Check for @bot mention in entities
                has_mention = False
                if message.entities:
                    for entity in message.entities:
                        if entity.type == "mention" and message.text:
                            mention_text = message.text[entity.offset:entity.offset + entity.length]
                            if "cloptbot_bot" in mention_text.lower():
                                has_mention = True
                                break
                
                # Note: is_reply_to_bot already handled above
                # Check other triggers
                should_respond, reason = self._should_respond_in_group(content, has_mention)
                
                if not should_respond:
                    logger.debug(f"Ignoring group message: {reason}")
                    return
                
                logger.info(f"Responding to group message: {reason}")
        
        # Check if this is the admin (owner) - ID from allowFrom
        is_admin = False
        allow_list = getattr(self.config, "allow_from", [])
        
        # For channel posts: check if sender_chat.id matches allowFrom OR if user.id matches
        # (Channel posts come from sender_chat, not from user directly)
        if is_channel_post and message.sender_chat:
            sender_chat_id = str(message.sender_chat.id)
            logger.info(f"Channel post detected: sender_chat.id={sender_chat_id}, user.id={user.id}")
            
            # Check if the channel itself is in allowFrom (could be positive or negative ID)
            # Try both the raw ID and absolute value
            if allow_list and (sender_chat_id in allow_list or str(abs(message.sender_chat.id)) in allow_list):
                is_admin = True
                logger.info(f"✅ Channel post from ALLOWED channel: {sender_chat_id}")
            else:
                logger.warning(f"❌ Channel post from UNALLOWED channel: {sender_chat_id} (not in {allow_list})")
        
        # Also check user.id (for regular messages and private chats)
        if allow_list and str(user.id) in allow_list:
            is_admin = True
        
        # Build metadata
        metadata = {
            "message_id": message.message_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "chat_id": message.chat.id,  # For chat isolation
            "is_group": is_group,
            "is_admin": is_admin  # NEW: distinguish admin from regular users
        }
        
        # Add channel post metadata
        if is_channel_post:
            metadata["is_channel_post"] = True
            metadata["channel_title"] = message.sender_chat.title if message.sender_chat else "Unknown"
            metadata["reply_to"] = message.message_id  # Reply to the post
            
            # Save channel post to chat-specific history for context
            await self._save_channel_post(
                post_id=message.message_id,
                content=content,
                date=message.date.isoformat() if message.date else "Unknown",
                from_user=metadata["channel_title"],
                chat_id=message.chat.id
            )
        
        # React with 👀 to show we're processing
        await self.react(chat_id, message.message_id, "👀")
        
        # Forward to the message bus
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str(chat_id),
            content=content,
            media=media_paths,
            metadata=metadata,
            skip_allow_check=is_group  # Skip allowFrom check in groups
        )
    
    async def _on_channel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle channel posts."""
        if not update.channel_post:
            logger.debug("No channel_post in update")
            return
            
        post = update.channel_post
        chat_id = post.chat_id
        
        logger.info(f"Received channel post in chat_id={chat_id}, from_chat={post.chat.title if post.chat else 'Unknown'}")
        
        # Build content
        content_parts = []
        if post.text: content_parts.append(post.text)
        if post.caption: content_parts.append(post.caption)
        
        content = "\n".join(content_parts) or "[media]"
        
        # Trigger agent
        # We use a special sender_id to indicate channel context
        sender_id = f"channel_{chat_id}"
        
        # Instruction for the agent
        instruction = (
            f"Analyze this channel post from '{post.chat.title}':\n\n"
            f"{content}\n\n"
            "Provide a summary and an alternative perspective/critique."
        )
        
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str(chat_id),
            content=instruction,
            media=[],
            metadata={
                "type": "channel_post",
                "message_id": post.message_id,
                "reply_to": post.message_id
            },
            skip_allow_check=True  # Always allow channel posts
        )

    def _get_extension(self, media_type: str, mime_type: str | None) -> str:
        """Get file extension based on media type."""
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]
        
        type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}
        return type_map.get(media_type, "")
