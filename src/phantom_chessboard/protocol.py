"""Phantom Chessboard BLE protocol definitions.

This module contains the wire-protocol layer used by the standalone
``phantom_chessboard`` package. It performs no Bluetooth I/O: functions encode
commands, decode notifications, validate values, and convert chess positions
into Phantom's physical-board representation.

The interface uses dedicated characteristics for operating mode, movement
speed, board status, command/event traffic, and telemetry. The first byte on
the command/event characteristic is an opcode; common payloads are ASCII.

Phantom's new-game position format is a 10x10 file-major matrix with a one-cell
empty border. Interior rows represent files a..h and columns represent ranks
8..1. ``fen_to_phantom_matrix`` owns that conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


SERVICE_UUID = "fd31a840-22e7-11eb-adc1-0242ac120002"
MODE_UUID = "c08d3691-e60f-4467-b2d0-4a4b7c72777e"
STATUS_UUID = "acb6543c-92ca-11ee-b9d1-0242ac120002"
# Dedicated physical movement-speed characteristic.
SPEED_UUID = "acb646cc-92ca-11ee-b9d1-0242ac120002"
COMMAND_UUID = "cc68a66e-3bfa-4614-a77f-f46954a4c103"
TELEMETRY_UUID = "7b204548-40c4-11eb-adc1-0242ac120002"

STARTING_FEN = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR "
    "w KQkq - 0 1"
)


class MovementSpeed(str, Enum):
    """Phantom's five physical movement-speed profiles.

    ``SPEED_UUID`` accepts ASCII digits ``1`` through ``5``:
    ``1`` Silence, ``2`` Slow, ``3`` Medium, ``4`` Fast, ``5`` Blitz.
    """

    SILENCE = "1"
    SLOW = "2"
    MEDIUM = "3"
    FAST = "4"
    BLITZ = "5"

    @classmethod
    def parse(
        cls,
        value: "MovementSpeed | str | int",
    ) -> "MovementSpeed":
        """Normalise a profile name or protocol digit."""

        if isinstance(value, cls):
            return value

        normal = str(value).strip().lower()

        names = {
            "silence": cls.SILENCE,
            "slow": cls.SLOW,
            "medium": cls.MEDIUM,
            "fast": cls.FAST,
            "blitz": cls.BLITZ,
        }

        if normal in names:
            return names[normal]

        for speed in cls:
            if normal == speed.value:
                return speed

        raise ValueError(
            "Unsupported Phantom movement speed "
            f"{value!r}; expected silence, slow, medium, fast, blitz or 1..5"
        )


class Side(str, Enum):
    """Human player's colour in Phantom protocol terms.
    
    Phantom represents White and Black with the ASCII digits ``1`` and ``2``
    in side-selection messages. This enum gives higher layers readable names
    while keeping normalisation in one place.
    """
    WHITE = "white"
    BLACK = "black"

    @classmethod
    def parse(cls, value: "Side | str") -> "Side":
        """Normalise a side designator to :class:`Side`.
        
        Parameters
        ----------
        value:
            ``Side`` instance, colour name, FEN-style letter, or Phantom numeric
            side token.
        
        Returns
        -------
        Side
            Normalised side value.
        
        Raises
        ------
        ValueError
            If the value cannot be interpreted as White or Black.
        """
        if isinstance(value, cls):
            return value

        normal = str(value).strip().lower()

        if normal in {"white", "w", "1"}:
            return cls.WHITE

        if normal in {"black", "b", "2"}:
            return cls.BLACK

        raise ValueError(
            f"Unsupported side: {value!r}"
        )


class BoardState(str, Enum):
    """High-level board states derived from status notifications.
    
    These values are an application-facing abstraction over the exact text
    emitted by the firmware. Unknown strings remain observable as
    ``BoardState.UNKNOWN`` rather than being discarded.
    """
    UNKNOWN = "UNKNOWN"
    HOME = "HOME"
    IDLE = "IDLE"
    STARTING_GAME = "STARTING_GAME"
    SETTING_UP = "SETTING_UP"
    MANAGING_MISMATCH = "MANAGING_MISMATCH"
    SNAPPING = "SNAPPING"
    WAITING_SIDE = "WAITING_SIDE"
    PLAYING = "PLAYING"
    ENDING_GAME = "ENDING_GAME"
    CALIBRATING = "CALIBRATING"
    HOMING = "HOMING"


@dataclass(frozen=True)
class PhantomEvent:
    """Base type for every decoded Phantom notification.
    
    ``raw`` preserves the exact bytes received from the board so unknown events
    can be inspected without losing information.
    """
    raw: bytes


@dataclass(frozen=True)
class StatusEvent(PhantomEvent):
    """Human-readable status notification from ``STATUS_UUID``.
    
    ``text`` is the exact ASCII status and ``state`` is the recognised
    high-level interpretation when one exists.
    """
    text: str
    state: BoardState = BoardState.UNKNOWN


@dataclass(frozen=True)
class MoveEvent(PhantomEvent):
    """Physical chess move reported by the board.
    
    ``notation`` preserves Phantom's ``-`` normal-move and ``x`` capture
    separator. Source/destination fields are split out for consumers that do
    not want to parse the string.
    """
    notation: str
    from_square: str
    to_square: str
    is_capture: bool

    @property
    def uci(self) -> str:
        """Return coordinate-only UCI notation for chess logic.
        
        Promotion suffixes are not included; this property returns only source and
        destination coordinates.
        """
        return (
            self.from_square
            + self.to_square
        )


@dataclass(frozen=True)
class CorrectionEvent(PhantomEvent):
    """Physical-position correction requested by Phantom.
    
    Example: ``R b6-a1`` means a white rook is physically detected at ``b6``
    while Phantom expects that rook at ``a1``.
    """
    piece: str
    actual_square: str
    required_square: str

    @property
    def instruction(self) -> str:
        """Return the compact correction string used by Phantom status output."""
        return (
            f"{self.piece} "
            f"{self.actual_square}-"
            f"{self.required_square}"
        )


@dataclass(frozen=True)
class CleanEvent(PhantomEvent):
    """Notification that sensed and expected physical positions match."""
    text: str


@dataclass(frozen=True)
class AckEvent(PhantomEvent):
    """Short binary acknowledgement emitted on the command/event channel."""
    code: int


@dataclass(frozen=True)
class ProtocolEvent(PhantomEvent):
    """Fallback event for an unknown or not-yet-decoded protocol message.
    
    Unknown messages are surfaced instead of ignored so callers can inspect the
    raw opcode and payload.
    """
    opcode: int
    payload: bytes


_STATUS_STATES = {
    "HOME": BoardState.HOME,
    "Idle": BoardState.IDLE,
    "Starting Game": (
        BoardState.STARTING_GAME
    ),
    "Setting Up": BoardState.SETTING_UP,
    # Treat the forms with and without a trailing ellipsis as the same state.
    "Managing Mismatch": (
        BoardState.MANAGING_MISMATCH
    ),
    "Managing Mismatch...": (
        BoardState.MANAGING_MISMATCH
    ),
    "Snapping Pieces": BoardState.SNAPPING,
    "Snap to Center": BoardState.SNAPPING,
    "Waiting Side": BoardState.WAITING_SIDE,
    "Board Playing": BoardState.PLAYING,
    "BLE Playing": BoardState.PLAYING,
    "Ending Game": BoardState.ENDING_GAME,
    "Calibrating": BoardState.CALIBRATING,
    "Homing": BoardState.HOMING,
}

_MOVE_RE = re.compile(
    r"^M 1 ([a-h][1-8])([-x])"
    r"([a-h][1-8])$"
)

_CORRECTION_RE = re.compile(
    r"^([KQRBNPkqrbnp]) "
    r"([a-h][1-8])-([a-h][1-8])$"
)

_MOTOR_RE = re.compile(
    r"^([a-h][1-8])([-x])"
    r"([a-h][1-8])$"
)


def _expand_fen_board(
    fen: str,
) -> dict[tuple[int, int], str]:
    """Expand the piece-placement field of FEN into square contents.
    
    The dictionary key is ``(file_index, rank)`` where file index zero means
    file ``a`` and rank uses normal chess numbering 1..8. Non-placement FEN
    fields are intentionally ignored because the physical matrix only describes
    piece locations.
    
    Raises
    ------
    ValueError
        If the piece-placement field is malformed.
    """
    placement = fen.strip().split()[0]
    ranks = placement.split("/")

    if len(ranks) != 8:
        raise ValueError(
            "FEN must contain eight ranks"
        )

    board: dict[
        tuple[int, int],
        str,
    ] = {}

    for rank_offset, rank_text in enumerate(
        ranks
    ):
        rank = 8 - rank_offset
        file_index = 0

        for token in rank_text:
            if token.isdigit():
                count = int(token)

                if not 1 <= count <= 8:
                    raise ValueError(
                        "Invalid FEN digit: "
                        f"{token!r}"
                    )

                file_index += count
                continue

            if token not in "prnbqkPRNBQK":
                raise ValueError(
                    "Invalid FEN piece: "
                    f"{token!r}"
                )

            if file_index >= 8:
                raise ValueError(
                    "FEN rank is too wide"
                )

            board[
                (file_index, rank)
            ] = token
            file_index += 1

        if file_index != 8:
            raise ValueError(
                f"FEN rank {rank} expands "
                f"to {file_index} files, "
                "expected 8"
            )

    return board


def fen_to_phantom_matrix(
    fen: str,
) -> str:
    """Convert FEN into Phantom's 100-character physical position matrix.
    
    The matrix is a flattened 10x10 grid. The outer ring is ``.``. Interior
    rows correspond to files ``a`` through ``h`` and interior columns to ranks
    ``8`` through ``1``. Upper-case pieces are White and lower-case pieces are
    Black, matching FEN conventions.
    
    Returns
    -------
    str
        Exactly 100 matrix characters.
    
    Raises
    ------
    ValueError
        If the FEN piece-placement field is invalid.
    """
    board = _expand_fen_board(fen)

    rows = [".........."]

    for file_index in range(8):
        cells = ["."]

        for rank in range(
            8,
            0,
            -1,
        ):
            cells.append(
                board.get(
                    (file_index, rank),
                    ".",
                )
            )

        cells.append(".")
        rows.append(
            "".join(cells)
        )

    rows.append("..........")

    matrix = "".join(rows)

    if len(matrix) != 100:
        raise AssertionError(
            "Internal error: Phantom "
            f"matrix length={len(matrix)}"
        )

    return matrix


def encode_new_game_position(
    fen: str,
    human_side: Side | str,
) -> bytes:
    """Encode the opcode-0 new-game setup packet.
    
    Wire format is ``0x00 + 100-byte matrix + b",W"`` for a White human or
    ``b",B"`` for a Black human.
    """
    side = Side.parse(human_side)

    side_byte = (
        b"W"
        if side is Side.WHITE
        else b"B"
    )

    return (
        b"\x00"
        + fen_to_phantom_matrix(
            fen
        ).encode("ascii")
        + b","
        + side_byte
    )


def normalise_move_notation(
    move: str,
    *,
    capture: Optional[bool] = None,
) -> str:
    """Normalise a move to Phantom coordinate notation.
    
    Accepted forms are ``e2-e4``, ``f6xe4`` and compact ``e2e4``. When compact
    notation is supplied, ``capture=True`` selects ``x``; otherwise ``-`` is
    used.
    
    Raises
    ------
    ValueError
        If the move is not in a supported coordinate form.
    """
    value = move.strip().lower()
    match = _MOTOR_RE.fullmatch(
        value
    )

    if match:
        source, separator, target = (
            match.groups()
        )

        if capture is not None:
            separator = (
                "x"
                if capture
                else "-"
            )

        return (
            f"{source}"
            f"{separator}"
            f"{target}"
        )

    if re.fullmatch(
        r"[a-h][1-8][a-h][1-8]",
        value,
    ):
        source = value[:2]
        target = value[2:]
        separator = (
            "x"
            if capture
            else "-"
        )

        return (
            f"{source}"
            f"{separator}"
            f"{target}"
        )

    raise ValueError(
        "Unsupported move notation "
        f"{move!r}; expected e2-e4, "
        "f6xe4 or e2e4"
    )


def encode_motor_move(
    notation: str,
) -> bytes:
    """Encode a board-controlled physical move using opcode ``0x02``.
    
    Normal moves use ``0x02 + b"M d7-d5 E"``; captures use the same format
    with ``x`` as the separator, for example ``0x02 + b"M b4xd2 E"``.
    """
    normal = normalise_move_notation(
        notation
    )

    return (
        b"\x02M "
        + normal.encode("ascii")
        + b" E"
    )


def encode_acknowledge_human_move(
) -> bytes:
    """Encode the ``03 31`` acknowledgement for an accepted human move.
    
    Callers should send this only after accepting the move in their own game
    state.
    """
    return b"\x031"


def encode_side(
    side: Side | str,
) -> bytes:
    """Encode human-side selection.
    
    Values are ``0A 31`` for human White and ``0A 32`` for human Black.
    """
    parsed = Side.parse(side)

    return (
        b"\x0a1"
        if parsed is Side.WHITE
        else b"\x0a2"
    )


def encode_capture_preamble(
    side: Side | str,
) -> bytes:
    """Encode the side-dependent preamble for a board-controlled capture.
    
    White maps to ``09 31`` and Black maps to ``09 32``.
    """
    parsed = Side.parse(side)

    return (
        b"\x091"
        if parsed is Side.WHITE
        else b"\x092"
    )


def encode_recalibrate() -> bytes:
    """Encode the single-byte recalibration command ``0x07``."""
    return b"\x07"


def encode_snap_to_center() -> bytes:
    """Encode the snap-to-centre command ``0x0D``."""
    return b"\x0d"


def encode_reset_detection(
    fen: str,
) -> bytes:
    """Encode reset-detection as ``0x0E`` followed by ASCII FEN.
    
    This command instructs Phantom to reconcile its sensed physical position
    with an authoritative logical position.
    
    Raises
    ------
    ValueError
        If the supplied FEN string is empty.
    """
    value = fen.strip()

    if not value:
        raise ValueError(
            "FEN cannot be empty"
        )

    return (
        b"\x0e"
        + value.encode("ascii")
    )


def encode_movement_speed(
    speed: MovementSpeed | str | int,
) -> bytes:
    """Encode a speed selection for direct writing to ``SPEED_UUID``."""

    return (
        MovementSpeed.parse(
            speed
        )
        .value
        .encode("ascii")
    )


def encode_mode_play() -> bytes:
    """Return the mode-characteristic value for play mode: ASCII ``2``."""
    return b"2"


def encode_mode_home() -> bytes:
    """Return the mode-characteristic value for HOME mode: ASCII ``3``."""
    return b"3"


def decode_status_notification(
    data: bytes,
) -> PhantomEvent:
    """Decode a notification received from ``STATUS_UUID``.
    
    Correction strings such as ``R b6-a1`` become ``CorrectionEvent`` objects;
    normal text becomes ``StatusEvent``. Non-ASCII payloads are preserved in a
    ``ProtocolEvent`` rather than discarded.
    """
    raw = bytes(data)

    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return ProtocolEvent(
            raw=raw,
            opcode=-1,
            payload=raw,
        )

    match = _CORRECTION_RE.fullmatch(
        text
    )

    if match:
        piece, actual, required = (
            match.groups()
        )

        return CorrectionEvent(
            raw=raw,
            piece=piece,
            actual_square=actual,
            required_square=required,
        )

    return StatusEvent(
        raw=raw,
        text=text,
        state=_STATUS_STATES.get(
            text,
            BoardState.UNKNOWN,
        ),
    )


def decode_command_notification(
    data: bytes,
) -> PhantomEvent:
    """Decode a notification received from ``COMMAND_UUID``.
    
    Recognised incoming forms currently include human move events
    ``03 + b"M 1 ..."``, acknowledgements beginning ``06``, and clean-position
    reports beginning ``08``. Any unrecognised opcode is returned as a
    ``ProtocolEvent`` with its bytes intact.
    """
    raw = bytes(data)

    if not raw:
        return ProtocolEvent(
            raw=raw,
            opcode=-1,
            payload=b"",
        )

    opcode = raw[0]
    payload = raw[1:]

    if opcode == 0x03:
        try:
            text = payload.decode(
                "ascii"
            )
        except UnicodeDecodeError:
            text = ""

        match = _MOVE_RE.fullmatch(
            text
        )

        if match:
            (
                source,
                separator,
                target,
            ) = match.groups()

            return MoveEvent(
                raw=raw,
                notation=(
                    f"{source}"
                    f"{separator}"
                    f"{target}"
                ),
                from_square=source,
                to_square=target,
                is_capture=(
                    separator == "x"
                ),
            )

    if (
        opcode == 0x06
        and len(payload) >= 1
    ):
        return AckEvent(
            raw=raw,
            code=payload[0],
        )

    if opcode == 0x08:
        try:
            text = payload.decode(
                "ascii"
            )
        except UnicodeDecodeError:
            text = ""

        if text.startswith("CLEAN:"):
            return CleanEvent(
                raw=raw,
                text=text,
            )

    return ProtocolEvent(
        raw=raw,
        opcode=opcode,
        payload=payload,
    )
