"""Asynchronous driver for the Phantom Chessboard.

``PhantomBoard`` owns BLE discovery, connection lifecycle, GATT notification
subscriptions, protocol command writes, event delivery, and asynchronous
mechanical-state waits. Chess rules, legality, game state, and strategy are
left to the calling application.

Concurrency model
-----------------
The class is intended to live in one asyncio event loop. Bleak callbacks decode
notifications and update lightweight state immediately; decoded events are then
placed on an ``asyncio.Queue`` for application code. Long operations wait on
board-originated status and acknowledgement events rather than fixed sleeps.

Ensure that no other BLE client is connected to the board before calling
``connect()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import (
    AsyncIterator,
    Callable,
)
from typing import Optional

from bleak import (
    BleakClient,
    BleakScanner,
)
from bleak.backends.device import BLEDevice

from .protocol import (
    COMMAND_UUID,
    MODE_UUID,
    SERVICE_UUID,
    SPEED_UUID,
    STATUS_UUID,
    STARTING_FEN,
    AckEvent,
    BoardState,
    CleanEvent,
    CorrectionEvent,
    MovementSpeed,
    PhantomEvent,
    Side,
    StatusEvent,
    decode_command_notification,
    decode_status_notification,
    encode_acknowledge_human_move,
    encode_capture_preamble,
    encode_mode_home,
    encode_mode_play,
    encode_motor_move,
    encode_movement_speed,
    encode_new_game_position,
    encode_recalibrate,
    encode_reset_detection,
    encode_side,
    encode_snap_to_center,
)


EventHandler = Callable[
    [PhantomEvent],
    None,
]


class PhantomBoard:
    """High-level asynchronous interface to one Phantom Chessboard.
    
    Parameters
    ----------
    address:
        Optional BLE address. If omitted, discover by the advertised Phantom
        service UUID; service discovery is preferred because BLE addresses may
        not be stable on every host.
    scan_timeout:
        Maximum discovery/connect timeout in seconds.
    logger:
        Optional logger; a module logger is used when omitted.
    """

    def __init__(
        self,
        *,
        address: Optional[str] = None,
        scan_timeout: float = 15.0,
        logger: Optional[
            logging.Logger
        ] = None,
    ) -> None:
        """Initialise driver state without opening a BLE connection.
        
        Notification/event synchronisation uses monotonically increasing version
        counters so a stale status that happened before an operation cannot satisfy
        a wait for a new transition.
        """
        self.address = address
        self.scan_timeout = (
            scan_timeout
        )
        self.log = (
            logger
            or logging.getLogger(
                __name__
            )
        )

        self._client: Optional[
            BleakClient
        ] = None
        self._device: Optional[
            BLEDevice
        ] = None

        self._events: asyncio.Queue[
            PhantomEvent
        ] = asyncio.Queue()

        self._handlers: list[
            EventHandler
        ] = []

        self._last_status = ""
        self._state = (
            BoardState.UNKNOWN
        )
        self._status_version = 0
        self._status_changed = (
            asyncio.Event()
        )

        self._last_ack: Optional[
            int
        ] = None
        self._ack_version = 0
        self._ack_changed = (
            asyncio.Event()
        )

        self._clean_version = 0
        self._clean_changed = (
            asyncio.Event()
        )

        self._human_side: Optional[
            Side
        ] = None

        self._movement_speed: Optional[
            MovementSpeed
        ] = None

    @property
    def connected(self) -> bool:
        """Return ``True`` while the underlying Bleak client reports a live connection."""
        return bool(
            self._client is not None
            and self._client.is_connected
        )

    @property
    def state(self) -> BoardState:
        """Return the most recent recognised high-level ``BoardState``."""
        return self._state

    @property
    def last_status(self) -> str:
        """Return the most recent raw status text emitted by Phantom."""
        return self._last_status

    @property
    def human_side(
        self,
    ) -> Optional[Side]:
        """Return the human side currently known to the driver, if one has been selected."""
        return self._human_side

    @property
    def movement_speed(
        self,
    ) -> Optional[MovementSpeed]:
        """Return the most recently selected physical movement-speed profile."""
        return self._movement_speed

    def add_event_handler(
        self,
        handler: EventHandler,
    ) -> None:
        """Register a synchronous observer for every decoded event.
        
        Handlers execute in the driver's asyncio/Bleak context and should return
        quickly. Blocking or long-running consumers should use ``events()``.
        """
        self._handlers.append(
            handler
        )

    def remove_event_handler(
        self,
        handler: EventHandler,
    ) -> None:
        """Remove a previously registered event observer if present."""
        try:
            self._handlers.remove(
                handler
            )
        except ValueError:
            pass

    async def __aenter__(
        self,
    ) -> "PhantomBoard":
        """Connect the board and return this instance for ``async with`` usage."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        """Disconnect the board when leaving an ``async with`` block."""
        await self.disconnect()

    async def _find_device(
        self,
    ) -> BLEDevice:
        """Discover the target Phantom BLE peripheral.
        
        An explicit address is used when configured; otherwise discovery matches
        the production service UUID advertised by the board.
        
        Raises
        ------
        RuntimeError
            If no matching board appears before ``scan_timeout``.
        """
        if self.address:
            device = (
                await BleakScanner
                .find_device_by_address(
                    self.address,
                    timeout=(
                        self.scan_timeout
                    ),
                )
            )

            if device is None:
                raise RuntimeError(
                    "Phantom not found at "
                    f"{self.address}"
                )

            return device

        service = (
            SERVICE_UUID.lower()
        )

        device = (
            await BleakScanner
            .find_device_by_filter(
                lambda _device, adv:
                    service
                    in [
                        uuid.lower()
                        for uuid in (
                            adv.service_uuids
                            or []
                        )
                    ],
                timeout=(
                    self.scan_timeout
                ),
            )
        )

        if device is None:
            raise RuntimeError(
                "Phantom Chessboard is "
                "not advertising. Ensure "
                "the phone is disconnected."
            )

        return device

    async def connect(
        self,
    ) -> None:
        """Connect and subscribe to status and command/event notifications.
        
        Calling this method when already connected is harmless. If notification
        subscription fails after connection, the partial connection is closed
        before the exception is re-raised.
        """
        if self.connected:
            return

        self._device = (
            await self._find_device()
        )

        self.log.info(
            "Connecting to Phantom "
            "%s (%s)",
            self._device.name,
            self._device.address,
        )

        client = BleakClient(
            self._device,
            timeout=self.scan_timeout,
        )

        await client.connect()
        self._client = client

        try:
            await client.start_notify(
                STATUS_UUID,
                self._on_status,
            )

            await client.start_notify(
                COMMAND_UUID,
                self._on_command,
            )

        except Exception:
            await client.disconnect()
            self._client = None
            raise

        self.log.info(
            "Connected to Phantom %s",
            self._device.address,
        )

    async def disconnect(
        self,
    ) -> None:
        """Stop notifications and close the BLE connection.
        
        Teardown is intentionally tolerant of partially disconnected BlueZ/Bleak
        state so this method is safe during exception handling and shutdown.
        """
        client = self._client
        self._client = None

        if client is None:
            return

        try:
            if client.is_connected:
                try:
                    await client.stop_notify(
                        STATUS_UUID
                    )
                except Exception:
                    pass

                try:
                    await client.stop_notify(
                        COMMAND_UUID
                    )
                except Exception:
                    pass

                await client.disconnect()

        finally:
            self.log.info(
                "Disconnected from "
                "Phantom"
            )

    async def next_event(
        self,
    ) -> PhantomEvent:
        """Wait for and return the next decoded Phantom event."""
        return await self._events.get()

    async def events(
        self,
    ) -> AsyncIterator[
        PhantomEvent
    ]:
        """Yield decoded Phantom events indefinitely until the consumer task is cancelled."""
        while True:
            yield (
                await self.next_event()
            )

    def _emit(
        self,
        event: PhantomEvent,
    ) -> None:
        """Queue one decoded event and notify lightweight synchronous observers."""
        self._events.put_nowait(
            event
        )

        for handler in tuple(
            self._handlers
        ):
            try:
                handler(event)
            except Exception:
                self.log.exception(
                    "Phantom event "
                    "handler failed"
                )

    def _on_status(
        self,
        _sender,
        data: bytearray,
    ) -> None:
        """Handle one ``STATUS_UUID`` notification.
        
        Recognised state messages update ``last_status``/``state`` and advance the
        status-version counter used by asynchronous operation waits.
        """
        event = (
            decode_status_notification(
                bytes(data)
            )
        )

        if isinstance(
            event,
            StatusEvent,
        ):
            self._last_status = (
                event.text
            )

            if (
                event.state
                is not
                BoardState.UNKNOWN
            ):
                self._state = (
                    event.state
                )

            self._status_version += 1
            self._status_changed.set()

        elif isinstance(
            event,
            CorrectionEvent,
        ):
            self._status_version += 1
            self._status_changed.set()

        self._emit(event)

    def _on_command(
        self,
        _sender,
        data: bytearray,
    ) -> None:
        """Handle one ``COMMAND_UUID`` notification and update acknowledgement/clean signals."""
        event = (
            decode_command_notification(
                bytes(data)
            )
        )

        if isinstance(
            event,
            AckEvent,
        ):
            self._last_ack = (
                event.code
            )
            self._ack_version += 1
            self._ack_changed.set()

        if isinstance(
            event,
            CleanEvent,
        ):
            self._clean_version += 1
            self._clean_changed.set()

        self._emit(event)

    def _require_client(
        self,
    ) -> BleakClient:
        """Return the connected Bleak client or raise a clear lifecycle error."""
        if (
            not self.connected
            or self._client is None
        ):
            raise RuntimeError(
                "Phantom board is not "
                "connected"
            )

        return self._client

    async def _write_command(
        self,
        payload: bytes,
    ) -> None:
        """Write one already-encoded payload to the bidirectional command characteristic."""
        client = (
            self._require_client()
        )

        self.log.debug(
            "COMMAND -> %s",
            payload.hex(" "),
        )

        await client.write_gatt_char(
            COMMAND_UUID,
            payload,
            response=True,
        )

    async def _write_mode(
        self,
        payload: bytes,
    ) -> None:
        """Write a top-level operating-mode value to the dedicated mode characteristic."""
        client = (
            self._require_client()
        )

        await client.write_gatt_char(
            MODE_UUID,
            payload,
            response=True,
        )

    async def _wait_for_status_after(
        self,
        *,
        after_version: int,
        accepted: set[str],
        timeout: float,
    ) -> str:
        """Wait for a *new* accepted status after ``after_version``.
        
        Version gating prevents a pre-existing ``Board Playing`` value from
        completing a newly issued command prematurely.
        
        Raises
        ------
        TimeoutError
            If no accepted status is received before the timeout.
        """
        loop = (
            asyncio.get_running_loop()
        )
        deadline = (
            loop.time() + timeout
        )

        while True:
            if (
                self._status_version
                > after_version
                and self._last_status
                in accepted
            ):
                return (
                    self._last_status
                )

            remaining = (
                deadline
                - loop.time()
            )

            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting "
                    "for Phantom status "
                    f"{sorted(accepted)}; "
                    "last status was "
                    f"{self._last_status!r}"
                )

            self._status_changed.clear()

            await asyncio.wait_for(
                self._status_changed.wait(),
                timeout=remaining,
            )

    async def _wait_for_ack_after(
        self,
        *,
        after_version: int,
        code: int,
        timeout: float,
    ) -> None:
        """Wait for a *new* acknowledgement code after ``after_version``.
        
        Raises
        ------
        TimeoutError
            If the requested acknowledgement does not arrive in time.
        """
        loop = (
            asyncio.get_running_loop()
        )
        deadline = (
            loop.time() + timeout
        )

        while True:
            if (
                self._ack_version
                > after_version
                and self._last_ack
                == code
            ):
                return

            remaining = (
                deadline
                - loop.time()
            )

            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting "
                    "for Phantom ack "
                    f"0x{code:02x}"
                )

            self._ack_changed.clear()

            await asyncio.wait_for(
                self._ack_changed.wait(),
                timeout=remaining,
            )

    async def set_movement_speed(
        self,
        speed: MovementSpeed | str | int,
    ) -> None:
        """Select Phantom's physical movement-speed profile.

        Wire mapping: ``1`` Silence, ``2`` Slow, ``3`` Medium, ``4`` Fast and
        ``5`` Blitz. The setting can be changed during play.
        """

        parsed = MovementSpeed.parse(
            speed
        )
        client = self._require_client()

        await client.write_gatt_char(
            SPEED_UUID,
            encode_movement_speed(
                parsed
            ),
            response=True,
        )

        self._movement_speed = parsed

        self.log.info(
            "Phantom movement speed set to %s",
            parsed.name,
        )

    async def set_play_mode(
        self,
    ) -> None:
        """Switch the dedicated mode characteristic to play value ``2``."""
        await self._write_mode(
            encode_mode_play()
        )

    async def home(
        self,
        *,
        timeout: float = 30.0,
    ) -> None:
        """Return Phantom to HOME mode and wait for a new ``HOME`` status notification."""
        version = (
            self._status_version
        )

        await self._write_mode(
            encode_mode_home()
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={"HOME"},
                timeout=timeout,
            )
        )

    async def set_side(
        self,
        side: Side | str,
        *,
        wait_for_ack: bool = True,
        timeout: float = 5.0,
        retries: int = 1,
    ) -> None:
        """Tell Phantom which colour is controlled by the human player.
        
        By default the method waits for acknowledgement code ``06 04`` and retries
        once. The selected side is retained for reset and capture operations.
        """
        parsed = Side.parse(side)
        self._human_side = parsed

        for attempt in range(
            retries + 1
        ):
            ack_version = (
                self._ack_version
            )

            await self._write_command(
                encode_side(parsed)
            )

            if not wait_for_ack:
                return

            try:
                await (
                    self._wait_for_ack_after(
                        after_version=(
                            ack_version
                        ),
                        code=0x04,
                        timeout=timeout,
                    )
                )
                return

            except TimeoutError:
                if attempt >= retries:
                    raise

                self.log.warning(
                    "No side "
                    "acknowledgement; "
                    "retrying"
                )

    async def new_game(
        self,
        *,
        fen: str = STARTING_FEN,
        human_side: Side | str,
        movement_speed: MovementSpeed | str | int | None = None,
        setup_timeout: float = 300.0,
    ) -> None:
        """Initialise and reconcile a physical game position.
        
        Sequence: enter play mode, send opcode-0 position matrix, allow Phantom's
        own mismatch-management process to reconcile pieces, wait for ``Waiting
        Side``, optionally select movement speed, select the human side, then
        wait for ``Board Playing``. Applying speed while Phantom is still
        waiting for side selection guarantees the profile is active before play
        can begin. Correction events remain available through ``events()``.
        """
        parsed = Side.parse(
            human_side
        )
        self._human_side = parsed

        await self.set_play_mode()

        version = (
            self._status_version
        )

        await self._write_command(
            encode_new_game_position(
                fen,
                parsed,
            )
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Waiting Side"
                },
                timeout=setup_timeout,
            )
        )

        if movement_speed is not None:
            await self.set_movement_speed(
                movement_speed
            )

        version = (
            self._status_version
        )

        await self.set_side(
            parsed
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Board Playing",
                    "BLE Playing",
                },
                timeout=30.0,
            )
        )

    async def acknowledge_human_move(
        self,
    ) -> None:
        """Acknowledge a human move that the chess layer has accepted.
        
        The driver deliberately does not acknowledge ``MoveEvent`` automatically;
        ``chess_manager`` remains authoritative for legality and game state.
        """
        await self._write_command(
            encode_acknowledge_human_move()
        )

    async def make_move(
        self,
        notation: str,
        *,
        wait_for_completion: bool = True,
        timeout: float = 120.0,
        send_capture_preamble: bool = True,
    ) -> None:
        """Command a board-controlled physical move.
        
        Normal moves use ``-`` and captures use ``x``. Captures optionally send the
        opcode-09 preamble first. The method can wait for a subsequent
        playing status so mechanical execution remains asynchronous and event
        driven.
        
        Raises
        ------
        RuntimeError
            If a capture preamble is requested before the human side is known.
        TimeoutError
            If completion is requested but no playing status arrives.
        """
        is_capture = (
            "x"
            in notation.lower()
        )

        if (
            is_capture
            and send_capture_preamble
        ):
            if self._human_side is None:
                raise RuntimeError(
                    "Human side unknown; "
                    "call set_side()/"
                    "new_game() first"
                )

            await self._write_command(
                encode_capture_preamble(
                    self._human_side
                )
            )

        # Snapshot the status version only after the GATT write completes.
        # This ensures that status notifications received before the current
        # motor command cannot satisfy its completion wait.
        await self._write_command(
            encode_motor_move(
                notation
            )
        )

        version = (
            self._status_version
        )

        if wait_for_completion:
            await (
                self._wait_for_status_after(
                    after_version=(
                        version
                    ),
                    accepted={
                        "Board Playing",
                        "BLE Playing",
                    },
                    timeout=timeout,
                )
            )

    async def snap_to_center(
        self,
        *,
        timeout: float = 180.0,
    ) -> None:
        """Ask Phantom to centre pieces and wait until play resumes.
        
        The operation can take around 100 seconds, so the timeout is intentionally
        generous and completion is status-driven rather than a fixed sleep.
        """
        version = (
            self._status_version
        )

        await self._write_command(
            encode_snap_to_center()
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Board Playing",
                    "BLE Playing",
                },
                timeout=timeout,
            )
        )

    async def recalibrate(
        self,
        *,
        timeout: float = 90.0,
    ) -> None:
        """Run recalibration/homing and wait until play resumes."""
        version = (
            self._status_version
        )

        await self._write_command(
            encode_recalibrate()
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Board Playing",
                    "BLE Playing",
                },
                timeout=timeout,
            )
        )

    async def reset_detection(
        self,
        fen: str,
        *,
        human_side: (
            Side | str | None
        ) = None,
        timeout: float = 300.0,
    ) -> None:
        """Re-synchronise physical detection from an authoritative FEN.
        
        This is intended for reconnect/recovery. Phantom receives opcode ``0x0E``
        plus FEN, reconciles the physical position, asks for the player side again,
        and then returns to playing.
        
        Raises
        ------
        RuntimeError
            If no side is supplied and the driver has no remembered human side.
        """
        side = (
            Side.parse(human_side)
            if human_side
            is not None
            else self._human_side
        )

        if side is None:
            raise RuntimeError(
                "Human side unknown; "
                "supply human_side"
            )

        version = (
            self._status_version
        )

        await self._write_command(
            encode_reset_detection(
                fen
            )
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Waiting Side"
                },
                timeout=timeout,
            )
        )

        version = (
            self._status_version
        )

        await self.set_side(
            side
        )

        await (
            self._wait_for_status_after(
                after_version=version,
                accepted={
                    "Board Playing",
                    "BLE Playing",
                },
                timeout=30.0,
            )
        )
