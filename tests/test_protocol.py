"""Protocol-level regression tests for ``phantom_chessboard``.

The tests pin wire-format invariants so refactoring cannot silently change the
board matrix, motor command syntax, status mapping, or move decoding.
"""

from phantom_chessboard.protocol import (
    STARTING_FEN,
    BoardState,
    MovementSpeed,
    MoveEvent,
    decode_command_notification,
    decode_status_notification,
    encode_motor_move,
    encode_movement_speed,
    encode_new_game_position,
    fen_to_phantom_matrix,
)


def test_starting_matrix():
    """Verify that standard FEN maps to the 10x10 file-major matrix."""
    matrix = (
        fen_to_phantom_matrix(
            STARTING_FEN
        )
    )

    assert len(matrix) == 100

    rows = [
        matrix[index:index + 10]
        for index in range(
            0,
            100,
            10,
        )
    ]

    assert rows == [
        "..........",
        ".rp....PR.",
        ".np....PN.",
        ".bp....PB.",
        ".qp....PQ.",
        ".kp....PK.",
        ".bp....PB.",
        ".np....PN.",
        ".rp....PR.",
        "..........",
    ]


def test_new_game_packet():
    """Verify opcode, matrix length and human-side suffix in a new-game packet."""
    packet = (
        encode_new_game_position(
            STARTING_FEN,
            "white",
        )
    )

    assert len(packet) == 103
    assert packet[0] == 0x00
    assert packet[-2:] == b",W"


def test_motor_move():
    """Verify normal-move and capture motor command encodings."""
    assert (
        encode_motor_move("d7-d5")
        == b"\x02M d7-d5 E"
    )

    assert (
        encode_motor_move("f6xe4")
        == b"\x02M f6xe4 E"
    )


def test_human_move_decode():
    """Verify a physical move notification decodes into a typed ``MoveEvent``."""
    event = (
        decode_command_notification(
            b"\x03M 1 c3-e4"
        )
    )

    assert isinstance(
        event,
        MoveEvent,
    )
    assert event.uci == "c3e4"
    assert not event.is_capture


def test_mismatch_status_variants() -> None:
    """Both supported mismatch strings map to the same logical state."""
    without_ellipsis = decode_status_notification(
        b"Managing Mismatch"
    )
    with_ellipsis = decode_status_notification(
        b"Managing Mismatch..."
    )

    assert without_ellipsis.state is BoardState.MANAGING_MISMATCH
    assert with_ellipsis.state is BoardState.MANAGING_MISMATCH


def test_movement_speed_encoding():
    """Verify the five movement-speed encodings."""
    assert encode_movement_speed("silence") == b"1"
    assert encode_movement_speed("slow") == b"2"
    assert encode_movement_speed("medium") == b"3"
    assert encode_movement_speed("fast") == b"4"
    assert encode_movement_speed("blitz") == b"5"
    assert MovementSpeed.parse(4) is MovementSpeed.FAST
