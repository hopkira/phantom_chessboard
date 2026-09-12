"""Pure protocol encoding/decoding for the Phantom Chessboard."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


SERVICE_UUID = "fd31a840-22e7-11eb-adc1-0242ac120002"
MODE_UUID = "c08d3691-e60f-4467-b2d0-4a4b7c72777e"
STATUS_UUID = "acb6543c-92ca-11ee-b9d1-0242ac120002"
COMMAND_UUID = "cc68a66e-3bfa-4614-a77f-f46954a4c103"
TELEMETRY_UUID = "7b204548-40c4-11eb-adc1-0242ac120002"

STARTING_FEN = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR "
    "w KQkq - 0 1"
)


class Side(str, Enum):
    WHITE = "white"
    BLACK = "black"

    @classmethod
    def parse(cls, value: "Side | str") -> "Side":
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
    raw: bytes


@dataclass(frozen=True)
class StatusEvent(PhantomEvent):
    text: str
    state: BoardState = BoardState.UNKNOWN


@dataclass(frozen=True)
class MoveEvent(PhantomEvent):
    notation: str
    from_square: str
    to_square: str
    is_capture: bool

    @property
    def uci(self) -> str:
        return (
            self.from_square
            + self.to_square
        )


@dataclass(frozen=True)
class CorrectionEvent(PhantomEvent):
    piece: str
    actual_square: str
    required_square: str

    @property
    def instruction(self) -> str:
        return (
            f"{self.piece} "
            f"{self.actual_square}-"
            f"{self.required_square}"
        )


@dataclass(frozen=True)
class CleanEvent(PhantomEvent):
    text: str


@dataclass(frozen=True)
class AckEvent(PhantomEvent):
    code: int


@dataclass(frozen=True)
class ProtocolEvent(PhantomEvent):
    opcode: int
    payload: bytes


_STATUS_STATES = {
    "HOME": BoardState.HOME,
    "Idle": BoardState.IDLE,
    "Starting Game": (
        BoardState.STARTING_GAME
    ),
    "Setting Up": BoardState.SETTING_UP,
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
    """Convert FEN to Phantom's 100-char bordered file-major matrix."""
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
    return b"\x031"


def encode_side(
    side: Side | str,
) -> bytes:
    parsed = Side.parse(side)

    return (
        b"\x0a1"
        if parsed is Side.WHITE
        else b"\x0a2"
    )


def encode_capture_preamble(
    side: Side | str,
) -> bytes:
    """Observed 09 31 with human White; Black 09 32 is inferred."""
    parsed = Side.parse(side)

    return (
        b"\x091"
        if parsed is Side.WHITE
        else b"\x092"
    )


def encode_recalibrate() -> bytes:
    return b"\x07"


def encode_snap_to_center() -> bytes:
    return b"\x0d"


def encode_reset_detection(
    fen: str,
) -> bytes:
    value = fen.strip()

    if not value:
        raise ValueError(
            "FEN cannot be empty"
        )

    return (
        b"\x0e"
        + value.encode("ascii")
    )


def encode_mode_play() -> bytes:
    return b"2"


def encode_mode_home() -> bytes:
    return b"3"


def decode_status_notification(
    data: bytes,
) -> PhantomEvent:
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
