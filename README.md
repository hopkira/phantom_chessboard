# phantom-chessboard

## Version 0.3.0

- Supports both `Managing Mismatch` and `Managing Mismatch...` as the same
  `MANAGING_MISMATCH` state.
- The diagnostic CLI drains already-queued final events before disconnecting so
  completing status notifications remain visible after commands such as `reset`.
- Supports the five Phantom physical movement-speed profiles.
- Motor-move completion is synchronized against status changes that occur after
  the corresponding GATT command write completes.

Standalone asynchronous Python BLE interface for the Phantom Chessboard.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Smoke test

Ensure no other BLE client is connected to the board, then run:

```bash
phantom-board listen
```

The default is discovery by Phantom service UUID, so the BLE address does not
need to be hard-coded.

## Start a game

```bash
phantom-board new-game --side white
```

or:

```bash
phantom-board new-game --side black
```

The library waits through Phantom's mismatch-management sequence. Status and
correction events remain available concurrently through `events()`.

A movement speed can be selected when starting the game:

```bash
phantom-board new-game --side white --speed fast
```

## Other commands

```bash
phantom-board move e7-e5
phantom-board move f6xe4
phantom-board speed medium
phantom-board snap
phantom-board recalibrate
phantom-board home
```

Reset detection from FEN:

```bash
phantom-board reset \
  --side white \
  --fen 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2'
```

## Python API

```python
from phantom_chessboard import MovementSpeed, PhantomBoard

board = PhantomBoard()

await board.connect()
await board.new_game(
    human_side="white",
    movement_speed=MovementSpeed.FAST,
)
await board.acknowledge_human_move()
await board.set_side("white")
await board.make_move("d7-d5")
await board.make_move("f6xe4")
await board.set_movement_speed(MovementSpeed.MEDIUM)
await board.reset_detection(fen)
await board.snap_to_center()
await board.recalibrate()
await board.home()
await board.disconnect()
```

Incoming events are independent of the command API:

```python
async for event in board.events():
    print(event)
```

## GATT characteristics

- service: `fd31a840-22e7-11eb-adc1-0242ac120002`
- mode: `c08d3691-e60f-4467-b2d0-4a4b7c72777e`
- speed: `acb646cc-92ca-11ee-b9d1-0242ac120002`
- status: `acb6543c-92ca-11ee-b9d1-0242ac120002`
- command/event: `cc68a66e-3bfa-4614-a77f-f46954a4c103`
- telemetry: `7b204548-40c4-11eb-adc1-0242ac120002`

## Physical movement speed

The speed characteristic accepts one ASCII digit:

- `1` Silence
- `2` Slow
- `3` Medium
- `4` Fast
- `5` Blitz

```python
from phantom_chessboard import MovementSpeed

await board.set_movement_speed(MovementSpeed.FAST)
```

The setting can be changed during an active game.

## Capture moves

For board-controlled captures, `PhantomBoard.make_move()` sends a side-dependent
capture preamble before the motor command when `send_capture_preamble=True`.
The mapping is:

- human White: `09 31`
- human Black: `09 32`

Normal moves do not send this preamble.

## Move-completion synchronization

`PhantomBoard.make_move()` records the status version after the motor-command
GATT write returns. Only later `Board Playing` or `BLE Playing` notifications
can complete that move.
