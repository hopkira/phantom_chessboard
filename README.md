# Phantom Chessboard Python Bluetooth driver

A simple async Python BLE driver for the production Phantom Chessboard.

## Smoke test

Ensure the iPhone or Android is disconnected from Phantom:

```bash
phantom-board listen
```

The default is discovery by Phantom service UUID, so the BLE address does
not need to be hard-coded.

## Start a game

```bash
phantom-board new-game --side white
```

or:

```bash
phantom-board new-game --side black
```

The library waits through Phantom's own mismatch management. Status and
correction events are still available concurrently through `events()`.

## Other tests

```bash
phantom-board move e7-e5
phantom-board move f6xe4
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
from phantom_chessboard import PhantomBoard

board = PhantomBoard()

await board.connect()
await board.new_game(human_side="white")
await board.acknowledge_human_move()
await board.set_side("white")
await board.make_move("d7-d5")
await board.make_move("f6xe4")
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

## Known production characteristics

- service: `fd31a840-22e7-11eb-adc1-0242ac120002`
- mode: `c08d3691-e60f-4467-b2d0-4a4b7c72777e`
- status: `acb6543c-92ca-11ee-b9d1-0242ac120002`
- command/event: `cc68a66e-3bfa-4614-a77f-f46954a4c103`

## Capture caveat

The `09 31` preamble was directly captured before a computer-controlled
capture while the human was White. `09 32` for human Black is inferred from
Phantom's otherwise consistent 1/2 side encoding and has not yet been
directly captured.
