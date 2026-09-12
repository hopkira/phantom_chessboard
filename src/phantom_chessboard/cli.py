"""CLI for testing the Phantom library without ROS."""

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
            )
            print("Board Playing")
            await asyncio.Event().wait()

        elif args.command == "move":
            await board.make_move(
                args.move
            )
            print("Move completed")

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
        printer.cancel()

        with contextlib.suppress(
            asyncio.CancelledError
        ):
            await printer

        await board.disconnect()


def build_parser(
) -> argparse.ArgumentParser:
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
