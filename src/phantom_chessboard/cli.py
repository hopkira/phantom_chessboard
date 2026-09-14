"""Command-line diagnostics for the Phantom Chessboard driver.

The CLI exercises the public ``PhantomBoard`` API and prints decoded events so
connection, protocol, status, move, and acknowledgement behaviour can be
inspected from the command line.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging

from .board import PhantomBoard
from .protocol import (
    STARTING_FEN,
    CorrectionEvent,
    MoveEvent,
    StatusEvent,
)


async def print_events(
    board: PhantomBoard,
) -> None:
    """Print decoded board events until the task is cancelled.
    
    Known event types get concise human-readable output; unknown protocol events
    are still printed so new firmware behaviour remains observable.
    """
    async for event in board.events():
        if isinstance(
            event,
            MoveEvent,
        ):
            print(
                "MOVE "
                f"{event.notation} "
                f"(uci={event.uci})"
            )

        elif isinstance(
            event,
            CorrectionEvent,
        ):
            print(
                "CORRECTION "
                f"{event.instruction}"
            )

        elif isinstance(
            event,
            StatusEvent,
        ):
            print(
                "STATUS "
                f"{event.state.value}: "
                f"{event.text!r}"
            )

        else:
            print(
                "EVENT "
                f"{type(event).__name__}: "
                f"{event}"
            )


async def run(args) -> None:
    """Connect to Phantom and execute one parsed CLI subcommand.
    
    A background event-printer task runs for the lifetime of the connection and
    is cancelled cleanly before disconnect.
    """
    board = PhantomBoard(
        address=args.address or None,
    )

    await board.connect()
    print("Connected")

    printer = asyncio.create_task(
        print_events(board)
    )

    try:
        if args.command == "listen":
            await asyncio.Event().wait()

        elif (
            args.command
            == "new-game"
        ):
            await board.new_game(
                fen=args.fen,
                human_side=args.side,
                movement_speed=(
                    args.speed
                    or None
                ),
            )
            print("Board Playing")
            await asyncio.Event().wait()

        elif args.command == "move":
            await board.make_move(
                args.move
            )
            print("Move completed")

        elif args.command == "speed":
            await board.set_movement_speed(
                args.speed
            )
            print(
                "Movement speed set to "
                f"{args.speed}"
            )

        elif args.command == "ack":
            await (
                board
                .acknowledge_human_move()
            )
            print("Acknowledged")

        elif args.command == "side":
            await board.set_side(
                args.side
            )
            print(
                "Human side set to "
                f"{args.side}"
            )

        elif args.command == "snap":
            await (
                board.snap_to_center()
            )
            print("Snap complete")

        elif (
            args.command
            == "recalibrate"
        ):
            await board.recalibrate()
            print(
                "Recalibration complete"
            )

        elif args.command == "reset":
            await (
                board.reset_detection(
                    args.fen,
                    human_side=(
                        args.side
                    ),
                )
            )
            print(
                "Detection reset complete"
            )

        elif args.command == "home":
            await board.home()
            print("Board HOME")

    finally:
        # A high-level operation can complete on the same notification that
        # also placed its final status event on the diagnostic queue.  Give
        # the background printer a brief opportunity to consume that already
        # queued event before cancelling it; otherwise commands such as
        # ``reset`` can report completion without visibly printing the final
        # ``Board Playing`` status.  This delay is diagnostic-only and has no
        # effect on board/protocol completion semantics.
        await asyncio.sleep(0.10)

        printer.cancel()

        with contextlib.suppress(
            asyncio.CancelledError
        ):
            await printer

        await board.disconnect()


def build_parser(
) -> argparse.ArgumentParser:
    """Create the command-line parser and all supported diagnostic subcommands."""
    parser = argparse.ArgumentParser(
        description=(
            "Phantom Chessboard "
            "BLE utility"
        )
    )

    parser.add_argument(
        "--address",
        default="",
        help=(
            "Optional BLE address. "
            "If omitted, discover by "
            "service UUID."
        ),
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("listen")

    new_game = sub.add_parser(
        "new-game"
    )
    new_game.add_argument(
        "--side",
        choices=[
            "white",
            "black",
        ],
        required=True,
    )
    new_game.add_argument(
        "--fen",
        default=STARTING_FEN,
    )
    new_game.add_argument(
        "--speed",
        choices=[
            "silence",
            "slow",
            "medium",
            "fast",
            "blitz",
        ],
        default="",
        help=(
            "Optional physical movement speed "
            "to apply before play begins."
        ),
    )

    speed = sub.add_parser(
        "speed"
    )
    speed.add_argument(
        "speed",
        choices=[
            "silence",
            "slow",
            "medium",
            "fast",
            "blitz",
            "1",
            "2",
            "3",
            "4",
            "5",
        ],
    )

    move = sub.add_parser(
        "move"
    )
    move.add_argument(
        "move",
        help=(
            "Move such as e2-e4 "
            "or f6xe4"
        ),
    )

    sub.add_parser("ack")

    side = sub.add_parser("side")
    side.add_argument(
        "side",
        choices=[
            "white",
            "black",
        ],
    )

    sub.add_parser("snap")
    sub.add_parser(
        "recalibrate"
    )

    reset = sub.add_parser(
        "reset"
    )
    reset.add_argument(
        "--side",
        choices=[
            "white",
            "black",
        ],
        required=True,
    )
    reset.add_argument(
        "--fen",
        required=True,
    )

    sub.add_parser("home")

    return parser


def main() -> None:
    """Console-script entry point installed as ``phantom-board``."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        ),
    )

    args = (
        build_parser()
        .parse_args()
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
