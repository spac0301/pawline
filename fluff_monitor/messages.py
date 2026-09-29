"""Messages shared by the stable relay and replaceable metadata worker."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class WsMessage:
    """One complete WebSocket message on a watched connection."""
    direction: str  # "c2s" (client to server) or "s2c" (server to client)
    text: str | bytes  # The asynchronous observer retains UTF-8 bytes.
    ts: float
    conn: int
    transport: str = "websocket"


