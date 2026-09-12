from phantom_chessboard.protocol import (
    STARTING_FEN,
    MoveEvent,
    decode_command_notification,
    encode_motor_move,
    encode_new_game_position,
    fen_to_phantom_matrix,
)


def test_starting_matrix():
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
    assert (
        encode_motor_move("d7-d5")
        == b"\x02M d7-d5 E"
    )

    assert (
        encode_motor_move("f6xe4")
        == b"\x02M f6xe4 E"
    )


def test_human_move_decode():
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
