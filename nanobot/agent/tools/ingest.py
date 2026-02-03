"""Tool for ingesting chat history."""

import json
import random
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool as BaseTool


class IngestHistoryTool(BaseTool):
    """
    Tool to ingest and analyze chat history from a JSON file.
    """
    
    name = "ingest_history"
    description = (
        "Ingest chat history from a JSON file to understand style and context. "
        "Returns a summary and samples for analysis."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the JSON history file."
            },
            "sample_size": {
                "type": "integer",
                "description": "Number of random samples to return.",
                "default": 20
            }
        },
        "required": ["file_path"]
    }
    
    async def execute(self, file_path: str, sample_size: int = 20) -> str:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File not found at {file_path}"
            
        try:
            print(f"Ingesting history from {file_path}...")
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            
            messages = []
            
            # Handle different formats
            if isinstance(data, list):
                # Simple list of message objects
                messages = data
            elif isinstance(data, dict) and "messages" in data:
                # Telegram export format
                messages = data["messages"]
            
            print(f"Found {len(messages)} raw items. Filtering for text...")
            
            # Filter for text messages
            # Extract text from both "text" field and "text_entities"
            text_messages = []
            for m in messages:
                if not isinstance(m, dict):
                    continue
                
                # Skip service messages
                if m.get("type") == "service":
                    continue
                
                text = m.get("text", "")
                
                # Handle text_entities (Telegram export format)
                if not text and "text_entities" in m:
                    text_parts = []
                    for entity in m["text_entities"]:
                        if isinstance(entity, dict) and "text" in entity:
                            text_parts.append(entity["text"])
                    text = "".join(text_parts)
                
                # Only include if there's actual text
                if text and isinstance(text, str) and len(text.strip()) > 0:
                    m["text"] = text  # Normalize text field
                    text_messages.append(m)
            
            if not text_messages:
                return "No text messages found in the file."
                
            total_count = len(text_messages)
            print(f"Found {total_count} text messages. Sampling...")
            
            # Get last N messages
            last_n = text_messages[-10:]
            
            # Get random samples
            samples = random.sample(text_messages, min(sample_size, total_count))
            
            # Format output
            output = [
                f"Successfully ingested {total_count} messages.",
                "",
                "## Recent Messages",
            ]
            
            for m in last_n:
                date = m.get("date", "Unknown date")
                text = m.get("text", "")[:200]
                output.append(f"- [{date}] {text}")
                
            output.append("")
            output.append("## Random Samples")
            
            for m in samples:
                text = m.get("text", "")
                output.append(f"- {text}")
            
            print("Ingestion complete. Returning summary.")
            return "\n".join(output)
            
        except json.JSONDecodeError:
            print("Error: Invalid JSON file.")
            return "Error: Invalid JSON file."
        except Exception as e:
            print(f"Error ingesting history: {str(e)}")
            return f"Error ingesting history: {str(e)}"
