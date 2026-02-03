"""YouTube video summary tool."""

import re
from loguru import logger
from nanobot.agent.tools.base import Tool

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
    except ImportError:
        # Fallback for older versions
        TranscriptsDisabled = Exception
        NoTranscriptFound = Exception
    HAS_YOUTUBE = True
except ImportError:
    HAS_YOUTUBE = False
    TranscriptsDisabled = Exception
    NoTranscriptFound = Exception
    logger.warning("youtube-transcript-api not installed. YouTube summary will not work.")


class YouTubeSummaryTool(Tool):
    """Tool for extracting and summarizing YouTube video transcripts."""
    
    name = "youtube_summary"
    description = (
        "Extract transcript from a YouTube video URL and return it for analysis. "
        "Use this when someone shares a YouTube link and you need to understand the content. "
        "Returns the full transcript which you can then analyze and summarize."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The YouTube video URL (youtube.com/watch?v=... or youtu.be/...)"
            }
        },
        "required": ["url"]
    }
    
    def _extract_video_id(self, url: str) -> str | None:
        """Extract video ID from various YouTube URL formats."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/)([^&\n?#]+)',
            r'youtube\.com\/shorts\/([^&\n?#]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    async def execute(self, url: str) -> str:
        """Extract transcript from YouTube video."""
        if not HAS_YOUTUBE:
            return "Error: youtube-transcript-api not installed. Run: pip install youtube-transcript-api"
        
        try:
            # Extract video ID
            video_id = self._extract_video_id(url)
            if not video_id:
                return f"Error: Could not extract video ID from URL: {url}"
            
            logger.info(f"Extracting transcript for YouTube video: {video_id}")
            
            # Try to get transcript (prefer Russian, fallback to English or any available)
            try:
                # Try to get transcript with language preference
                try:
                    # Try Russian first, then English
                    logger.debug("Attempting to get transcript in Russian/English...")
                    transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en'])
                    logger.debug("Got transcript in preferred language")
                except Exception as lang_err:
                    # Fallback to any available language
                    logger.debug(f"Language preference failed ({lang_err}), trying any available language...")
                    transcript_data = YouTubeTranscriptApi.get_transcript(video_id)
                    logger.debug("Got transcript in default language")
                    
            except (TranscriptsDisabled, NoTranscriptFound) as e:
                logger.error(f"No transcript available: {e}")
                return f"Error: No transcript available for this video. ({str(e)})"
            except Exception as e:
                logger.error(f"Unexpected error getting transcript: {e}")
                return f"Error: Failed to get transcript: {str(e)}"
            
            # Format transcript
            text_parts = []
            for entry in transcript_data:
                text_parts.append(entry['text'])
            
            full_transcript = ' '.join(text_parts)
            
            # Limit length for context (approx 4000 words)
            max_chars = 16000
            if len(full_transcript) > max_chars:
                full_transcript = full_transcript[:max_chars] + "...\n[Transcript truncated due to length]"
            
            logger.info(f"Successfully extracted transcript ({len(text_parts)} segments, {len(full_transcript)} chars)")
            
            return f"YouTube Video Transcript (ID: {video_id}):\n\n{full_transcript}"
            
        except Exception as e:
            logger.error(f"Failed to extract YouTube transcript: {e}")
            return f"Error extracting YouTube transcript: {str(e)}"
