"""
Robust Asyncio TCP Server and UDP Redirect Announcer for Wi-Fi Dongle.
Features automated watchdog, periodic UDP keepalive, and instant self-healing reconnect.
"""

import asyncio
import logging
import socket
import struct
import time
from datetime import datetime, timezone
from typing import Optional

from .config import (
    DEVADDR,
    DEVCODE,
    DONGLE_IP,
    DONGLE_UDP_PORT,
    HOST_IP,
    REQUEST_TIMEOUT,
    TCP_PORT,
    UDP_REDIRECT_INTERVAL,
)
from .protocol_pi30 import build_request, parse_response_frame

logger = logging.getLogger(__name__)

FC_HEARTBEAT = 1
FC_FORWARD2DEVICE = 4
HEADER_SIZE = 8
WIRE_LEN_OFFSET = 6
HEARTBEAT_INTERVAL = 30.0       # Heartbeat every 30 seconds
UDP_KEEPALIVE_INTERVAL = 90.0   # Re-announce every 90s to keep dongle cloud-timer reset


class TIDCounter:
    """Thread-safe transaction ID counter wrapping at 0xFFFF."""

    def __init__(self):
        self._tid = 0

    def next(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        if self._tid == 0:
            self._tid = 1
        return self._tid


def encode_header(tid: int, devcode: int, total_len: int, devaddr: int, fcode: int) -> bytes:
    wire_len = total_len - WIRE_LEN_OFFSET
    return struct.pack(">HHHBB", tid, devcode, wire_len, devaddr, fcode)


def decode_header(data: bytes) -> tuple[int, int, int, int, int]:
    return struct.unpack(">HHHBB", data[:HEADER_SIZE])


def build_heartbeat_request(tid: int, interval: int = 30) -> bytes:
    now = datetime.now(timezone.utc)
    payload = bytes([
        (now.year - 2000) & 0xFF,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
    ]) + struct.pack(">H", interval)
    total_len = HEADER_SIZE + len(payload)
    return encode_header(tid, 0, total_len, 1, FC_HEARTBEAT) + payload


class CollectorConnection:
    """Represents a single active TCP connection from the dongle."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peername = writer.get_extra_info("peername")
        self.remote_ip = self.peername[0] if self.peername else "unknown"
        self.collector_pn: str = ""
        self.connected_at: float = time.time()
        self.last_seen: float = time.time()
        self._pending: dict[int, asyncio.Future] = {}
        self._tid = TIDCounter()
        self.closed: bool = False

    def close(self):
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Dongle connection closed"))
        self._pending.clear()


class CollectorServer:
    """Manages listening on port 8899 and maintains continuous connection with the dongle."""

    def __init__(self):
        self.host = "0.0.0.0"
        self.port = TCP_PORT
        self.dongle_ip = DONGLE_IP
        self.dongle_udp_port = DONGLE_UDP_PORT

        self._server: Optional[asyncio.Server] = None
        self._conn: Optional[CollectorConnection] = None
        self._send_lock = asyncio.Lock()

        self._heartbeat_task: Optional[asyncio.Task] = None
        self._read_task: Optional[asyncio.Task] = None
        self._udp_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    @property
    def connected_time(self) -> float:
        return self._conn.connected_at if self._conn else 0.0

    @property
    def collector_pn(self) -> str:
        return self._conn.collector_pn if self._conn else ""

    @property
    def remote_ip(self) -> str:
        return self._conn.remote_ip if self._conn else ""

    @property
    def last_seen(self) -> float:
        return self._conn.last_seen if self._conn else 0.0

    async def start(self):
        """Start TCP Server and initial UDP announcer."""
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("Collector TCP Server listening on %s:%d", self.host, self.port)
        self._udp_task = asyncio.create_task(self._udp_announcer_loop())

    async def stop(self):
        """Clean shutdown."""
        if self._udp_task:
            self._udp_task.cancel()
        await self._close_current_connection()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Collector TCP Server stopped")

    def _send_udp_redirect(self):
        """Send unicast redirect packet to dongle IP."""
        cmd = f"set>server={HOST_IP}:{TCP_PORT};".encode("ascii")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.0)
            sock.sendto(cmd, (self.dongle_ip, self.dongle_udp_port))
            sock.close()
            logger.debug("Sent UDP redirect to %s:%d -> %s", self.dongle_ip, self.dongle_udp_port, cmd.decode())
        except Exception as e:
            logger.warning("UDP redirect error: %s", e)

    async def force_reconnect(self):
        """Explicit self-healing trigger: tears down stale socket and immediately redirects dongle."""
        logger.info("Watchdog triggered: forcing connection reset and re-announcing...")
        await self._close_current_connection()
        self._send_udp_redirect()

    async def _udp_announcer_loop(self):
        """Periodically announce server to dongle to prevent firmware cloud-reversion."""
        self._send_udp_redirect()
        await asyncio.sleep(4.0)

        last_keepalive = time.time()
        while True:
            try:
                now = time.time()
                if not self.connected:
                    logger.info("Dongle not connected. Sending UDP redirect to %s...", self.dongle_ip)
                    self._send_udp_redirect()
                    await asyncio.sleep(UDP_REDIRECT_INTERVAL)
                else:
                    # While connected, send a periodic UDP refresh every 90s to keep dongle cloud timer reset
                    if now - last_keepalive >= UDP_KEEPALIVE_INTERVAL:
                        logger.debug("Sending periodic UDP keepalive to dongle...")
                        self._send_udp_redirect()
                        last_keepalive = now
                    await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("UDP loop error: %s", e)
                await asyncio.sleep(5.0)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming connection from the dongle."""
        peername = writer.get_extra_info("peername")
        logger.info("Incoming connection from %s", peername)

        if self._conn and not self._conn.closed and (time.time() - self._conn.connected_at < 3.0):
            logger.warning("Rejecting rapid duplicate connection from %s", peername)
            writer.close()
            return

        if self._conn:
            logger.info("Replacing existing connection with new from %s", peername)
            await self._close_current_connection()

        conn = CollectorConnection(reader, writer)
        self._conn = conn

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(conn))
        self._read_task = asyncio.create_task(self._read_loop(conn))

        try:
            await self._read_task
        except asyncio.CancelledError:
            pass
        finally:
            if self._conn is conn:
                await self._close_current_connection()

    async def _close_current_connection(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Dongle connection terminated")

    async def _heartbeat_loop(self, conn: CollectorConnection):
        """Periodic FC=1 heartbeat."""
        try:
            await self._send_heartbeat(conn)
            while not conn.closed:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._send_heartbeat(conn)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Heartbeat error: %s", e)

    async def _send_heartbeat(self, conn: CollectorConnection):
        if conn.closed:
            return
        tid = conn._tid.next()
        frame = build_heartbeat_request(tid, int(HEARTBEAT_INTERVAL))
        try:
            conn.writer.write(frame)
            await conn.writer.drain()
            logger.debug("Sent Heartbeat (TID=%d)", tid)
        except Exception as e:
            logger.warning("Failed to send heartbeat: %s", e)

    async def _read_loop(self, conn: CollectorConnection):
        """Read and dispatch incoming frames with a read timeout watchdog."""
        try:
            while not conn.closed:
                # If no packet received for 90s, connection is stale
                header_bytes = await asyncio.wait_for(conn.reader.readexactly(HEADER_SIZE), timeout=90.0)
                tid, devcode, wire_len, devaddr, fcode = decode_header(header_bytes)
                total_len = wire_len + WIRE_LEN_OFFSET
                payload_len = total_len - HEADER_SIZE

                payload = b""
                if payload_len > 0:
                    payload = await asyncio.wait_for(conn.reader.readexactly(payload_len), timeout=10.0)

                conn.last_seen = time.time()

                if fcode == FC_HEARTBEAT:
                    conn.collector_pn = payload[:14].decode("ascii", errors="replace").strip("\x00")
                    logger.info("Collector PN verified: %s (remote: %s)", conn.collector_pn, conn.remote_ip)

                elif fcode == FC_FORWARD2DEVICE:
                    fut = conn._pending.pop(tid, None)
                    if fut and not fut.done():
                        fut.set_result(payload)
                    else:
                        logger.debug("Received unsolicited/late FC=4 frame TID=%d", tid)

                else:
                    logger.debug("Received FC=%d frame TID=%d len=%d", fcode, tid, total_len)

        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            logger.info("Dongle socket read closed or timed out (EOF/watchdog)")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Read loop error: %s", e)

    async def send_inverter_command(self, cmd: str, devaddr: int = DEVADDR, timeout: float = REQUEST_TIMEOUT) -> str:
        """Send a PI30 command to the inverter through the active collector connection."""
        conn = self._conn
        if not conn or conn.closed:
            raise ConnectionError("Inverter Wi-Fi dongle is not connected")

        raw_pi30 = build_request(cmd)

        async with self._send_lock:
            if conn.closed:
                raise ConnectionError("Connection closed while acquiring lock")

            tid = conn._tid.next()
            total_len = HEADER_SIZE + len(raw_pi30)
            fwd_header = encode_header(tid, DEVCODE, total_len, devaddr, FC_FORWARD2DEVICE)
            full_frame = fwd_header + raw_pi30

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            conn._pending[tid] = fut

            try:
                conn.writer.write(full_frame)
                await conn.writer.drain()
                logger.debug("TX Inverter %s (TID=%d, %d bytes)", cmd, tid, len(full_frame))

                resp_bytes = await asyncio.wait_for(fut, timeout=timeout)
                return parse_response_frame(resp_bytes)
            except asyncio.TimeoutError:
                conn._pending.pop(tid, None)
                raise TimeoutError(f"Timeout waiting for inverter response to {cmd} ({timeout}s)")
            except Exception:
                conn._pending.pop(tid, None)
                raise
