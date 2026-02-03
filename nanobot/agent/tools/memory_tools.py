"""Tools for memory and persona management."""

from pathlib import Path
from nanobot.agent.tools.base import Tool as BaseTool
from nanobot.agent.memory import MemoryStore
from nanobot.agent.persona import PersonaManager


class AddToMemoryTool(BaseTool):
    """Tool to add important information to long-term memory."""
    
    name = "add_to_memory"
    description = "Add important facts, context, or learnings to long-term memory."
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The content to add to memory."
            }
        },
        "required": ["content"]
    }
    
    def __init__(self, workspace: Path):
        self.memory = MemoryStore(workspace)
        
    async def execute(self, content: str) -> str:
        current = self.memory.read_long_term()
        new_content = f"{current}\n\n- {content}" if current else f"- {content}"
        self.memory.write_long_term(new_content)
        return "Successfully added to long-term memory."


class UpdatePersonaTool(BaseTool):
    """Tool to update the agent's persona."""
    
    name = "update_persona"
    description = "Update the agent's persona file with new style guidelines or character traits."
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The full content of the persona file (markdown)."
            }
        },
        "required": ["content"]
    }
    
    def __init__(self, workspace: Path):
        self.persona = PersonaManager(workspace)
        
    async def execute(self, content: str) -> str:
        self.persona.update_persona(content)
        return "Successfully updated persona."
