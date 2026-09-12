"""Reusable async BLE client for the Phantom Chessboard."""

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
    STATUS_UUID,
    STARTING_FEN,
    AckEvent,
    BoardState,
    CleanEvent,
    CorrectionEvent,
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
    """High-level async interface to one Phantom Chessboard."""

    def __init__(
        self,
        *,
        address: Optional[str] = None,
        scan_timeout: float = 15.0,
        logger: Optional[
            logging.Logger
        ] = None,
    ) -> None:
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

    @property
    def connected(self) -> bool:
        return bool(
            self._client is not None
            and self._client.is_connected
        )

    @property
    def state(self) -> BoardState:
        return self._state

    @property
    def last_status(self) -> str:
        return self._last_status

    @property
    def human_side(
        self,
    ) -> Optional[Side]:
        return self._human_side

    def add_event_handler(
        self,
        handler: EventHandler,
    ) -> None:
        self._handlers.append(
            handler
        )

    def remove_event_handler(
        self,
        handler: EventHandler,
    ) -> None:
        try:
            self._handlers.remove(
                handler
            )
        except ValueError:
            pass

    async def __aenter__(
        self,
    ) -> "PhantomBoard":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        await self.disconnect()

    async def _find_device(
        self,
    ) -> BLEDevice:
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
        return await self._events.get()

    async def events(
        self,
    ) -> AsyncIterator[
        PhantomEvent
    ]:
        while True:
            yield (
                await self.next_event()
            )

    def _emit(
        self,
        event: PhantomEvent,
    ) -> None:
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

    async def set_play_mode(
        self,
    ) -> None:
        await self._write_mode(
            encode_mode_play()
        )

    async def home(
        self,
        *,
        timeout: float = 30.0,
    ) -> None:
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
        setup_timeout: float = 300.0,
    ) -> None:
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

        version = (
            self._status_version
        )

        await self._write_command(
            encode_motor_move(
                notation
            )
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
