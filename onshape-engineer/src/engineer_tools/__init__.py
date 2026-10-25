"""engineer-tools: deterministic core for a Lead/Worker Engineer agent pair (OpenClaw + Gemma + Onshape MCP)."""
from .config import EngineerConfig
from .engine import Engine, EngineError

__all__ = ["Engine", "EngineError", "EngineerConfig"]
__version__ = "0.1.0"
