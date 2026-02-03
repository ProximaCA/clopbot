"""Tool for analyzing music tracks."""

from nanobot.agent.tools.base import Tool


class MusicAnalysisTool(Tool):
    """
    Tool to analyze music tracks by searching for lyrics and context.
    """
    
    name = "analyze_music"
    description = (
        "Search for information about a music track, including lyrics, artist info, and context. "
        "Use this when someone shares a music track and you need to understand it deeply."
    )
    parameters = {
        "type": "object",
        "properties": {
            "track_name": {
                "type": "string",
                "description": "The name of the track"
            },
            "artist_name": {
                "type": "string",
                "description": "The name of the artist"
            }
        },
        "required": ["track_name", "artist_name"]
    }
    
    async def execute(self, track_name: str, artist_name: str) -> str:
        """
        Search for track information.
        
        Note: This is a placeholder that returns instructions to use web search.
        In a real implementation, you would call Genius API or similar.
        """
        print(f"Analyzing music: {track_name} by {artist_name}")
        
        # For now, instruct to use web search
        search_query = f"{track_name} {artist_name} lyrics genius"
        
        return (
            f"To analyze '{track_name}' by {artist_name}, "
            f"use the 'web_search' tool with query: '{search_query}' "
            f"to find lyrics and context on Genius or similar sites."
        )
