# Phantom Chessboard Python Bluetooth driver package

A simple async Python BLE driver for the production Phantom Chessboard.

## Installation

Clone the github directory and then:

```bash
cd ~/phantom_chessboard
python -m pip install -e .
```

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

## Disclaimer

The phantom_chessboard software is an independent, unofficial project and is not affiliated with, endorsed by, supported by, or developed in conjunction with Phantom Chessboard or its creators. Phantom Chessboard has had no involvement in the development of this software and has provided no proprietary source code, documentation, technical assistance, intellectual property, or other confidential information for its development.

The software is provided for experimental, educational and interoperability purposes and is used entirely at your own risk. No warranty is given that it will operate correctly with any particular Phantom Chessboard, firmware version, computer, Bluetooth adapter, or software configuration. The author accepts no responsibility for any loss, damage, malfunction, data loss, or other consequence arising from its use, including damage to a computer, chessboard or other connected equipment, subject always to the terms and limitations of the Apache License, Version 2.0 and applicable law.

No infringement of the copyright, patents, trademarks, trade secrets or other intellectual property rights of Phantom Chessboard, its designers, developers or other rights holders is intended. This project is not intended to reproduce, distribute or substitute for Phantom's firmware, applications or other proprietary software.

All product names, trademarks and registered trademarks remain the property of their respective owners. References to Phantom Chessboard are made solely to identify the hardware with which this software is intended to interoperate.

This notice is supplementary to, and does not alter or replace, the terms of the Apache License, Version 2.0 under which this software is distributed.
