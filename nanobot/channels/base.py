"""Base channel interface for chat platforms."""

from abc import ABC, abstractmethod
from typing import Any

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.
    
    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.
    """
    
    name: str = "base"
    
    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.
        
        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.bus = bus
        self._running = False
    
    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.
        
        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass
    
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.
        
        Args:
            msg: The message to send.
        """
        pass
    
    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.
        
        Args:
            sender_id: The sender's identifier.
        
        Returns:
            True if allowed, False otherwise.
        """
        from loguru import logger
        
        allow_list = getattr(self.config, "allow_from", [])
        
        # If no allow list, allow everyone
        if not allow_list:
            logger.debug(f"No allow list configured, allowing sender: {sender_id}")
            return True
        
        sender_str = str(sender_id)
        if sender_str in allow_list:
            logger.debug(f"Sender {sender_id} allowed (exact match)")
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    logger.debug(f"Sender {sender_id} allowed (part '{part}' matched)")
                    return True
        
        logger.warning(f"Sender {sender_id} BLOCKED by allowFrom filter. Allow list: {allow_list}")
        return False
    
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        skip_allow_check: bool = False
    ) -> None:
        """
        Handle an incoming message from the chat platform.
        
        This method checks permissions and forwards to the bus.
        
        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            skip_allow_check: If True, skip allowFrom check (e.g. for group chats).
        """
        from loguru import logger
        
        # In group chats, allow everyone. In private chats, check allowFrom.
        if not skip_allow_check and not self.is_allowed(sender_id):
            logger.warning(f"Message from {sender_id} blocked by allowFrom filter (private chat)")
            return
        
        if skip_allow_check:
            logger.debug(f"Skipping allowFrom check for {sender_id} (group chat)")
        
        logger.info(f"Publishing message to bus: sender={sender_id}, chat={chat_id}, content_preview={content[:50]}...")
        
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {}
        )
        
        await self.bus.publish_inbound(msg)
        logger.debug(f"Message published to bus successfully")
    
    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
