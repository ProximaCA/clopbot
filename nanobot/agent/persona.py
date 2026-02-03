"""Persona management for the agent."""

from pathlib import Path

class PersonaManager:
    """
    Manages the agent's persona and learned behavior.
    """
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.persona_file = workspace / "PERSONA.md"
        
    def get_persona(self) -> str:
        """Get the current persona content."""
        if self.persona_file.exists():
            return self.persona_file.read_text(encoding="utf-8")
        return ""
        
    def update_persona(self, content: str) -> None:
        """Update the persona content."""
        self.persona_file.write_text(content, encoding="utf-8")
