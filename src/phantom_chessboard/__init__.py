"""Public API for the standalone Phantom Chessboard package."""

from .board import PhantomBoard
from .protocol import (
    STARTING_FEN,
    AckEvent,
    BoardState,
    CleanEvent,
    CorrectionEvent,
    MovementSpeed,
    MoveEvent,
    PhantomEvent,
    ProtocolEvent,
    Side,
    StatusEvent,
    fen_to_phantom_matrix,
)

__all__ = [
    "PhantomBoard",
    "STARTING_FEN",
    "AckEvent",
    "BoardState",
    "CleanEvent",
    "CorrectionEvent",
    "MovementSpeed",
    "MoveEvent",
    "PhantomEvent",
    "ProtocolEvent",
    "Side",
    "StatusEvent",
    "fen_to_phantom_matrix",
]
