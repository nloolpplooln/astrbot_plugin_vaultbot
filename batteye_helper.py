import asyncio
import base64
import hashlib
import random
import socket
from typing import Any

from .gtaonline_helper import name_to_rid

BATTLEYE_SERVER_HOST = "51.89.97.102"
BATTLEYE_SERVER_PORT = 61455
BATTLEYE_TIMEOUT_SECONDS = 5


def configure_battleye(
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: int | None = None,
) -> None:
    """Configure BattlEye query target and timeout at runtime."""
    global BATTLEYE_SERVER_HOST, BATTLEYE_SERVER_PORT, BATTLEYE_TIMEOUT_SECONDS

    if isinstance(host, str) and host.strip():
        BATTLEYE_SERVER_HOST = host.strip()

    if port is not None:
        BATTLEYE_SERVER_PORT = int(port)

    if timeout_seconds is not None:
        BATTLEYE_TIMEOUT_SECONDS = int(timeout_seconds)


def compute_be_id(rid: int) -> str:
    """Compute BattlEye ID from Rockstar ID."""
    rid_base64 = base64.b64encode(str(rid).encode("utf-8")).decode("ascii")
    payload = f"BE{rid_base64}"
    return hashlib.md5(payload.encode("ascii")).hexdigest().lower()


def _decode_ban_data(ban_data: bytes) -> str:
    """Decode ban reason bytes with a few fallback encodings."""
    for encoding in ("ascii", "utf-8", "latin-1"):
        try:
            text = ban_data.decode(encoding, errors="replace").strip()
            if text:
                return text
        except Exception:
            continue
    return ban_data.hex()


class _BattlEyeProtocol(asyncio.DatagramProtocol):
    """Small protocol helper to capture one UDP response."""

    def __init__(self):
        self.transport: asyncio.DatagramTransport | None = None
        self.response: bytes | None = None
        self.future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if isinstance(transport, asyncio.DatagramTransport):
            self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if not self.future.done():
            self.response = data
            self.future.set_result(data)
        if self.transport and not self.transport.is_closing():
            self.transport.close()

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)
        if self.transport and not self.transport.is_closing():
            self.transport.close()

    def connection_lost(self, exc: Exception | None) -> None:
        if not self.future.done():
            if exc:
                self.future.set_exception(exc)
            elif self.response is None:
                self.future.set_exception(asyncio.TimeoutError("No UDP response received."))


async def query_battleye_ban_reason_by_rid(
    rid: int,
    timeout_seconds: int = BATTLEYE_TIMEOUT_SECONDS,
    host: str = BATTLEYE_SERVER_HOST,
    port: int = BATTLEYE_SERVER_PORT,
) -> str:
    """Query BattlEye server and return ban reason text; empty string means not banned."""
    transport: asyncio.DatagramTransport | None = None
    try:
        loop = asyncio.get_running_loop()
        protocol = _BattlEyeProtocol()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            family=socket.AF_INET,
            local_addr=("0.0.0.0", 0),
        )

        header = bytes(random.randint(0, 255) for _ in range(4))
        be_id = compute_be_id(rid)
        payload = header + be_id.encode("ascii")
        transport.sendto(payload, (host, port))

        response = await asyncio.wait_for(protocol.future, timeout=timeout_seconds)
        if len(response) <= 4:
            return ""

        return _decode_ban_data(response[4:])
    finally:
        if transport and not transport.is_closing():
            transport.close()


async def check_battleye_by_rid(rid: int) -> dict[str, Any]:
    """Return structured BattlEye ban check result for a RID."""
    reason = await query_battleye_ban_reason_by_rid(rid)
    return {
        "rid": rid,
        "is_banned": bool(reason),
        "ban_reason": reason,
    }


async def check_battleye_by_name(name: str) -> dict[str, Any]:
    """Resolve name to RID first, then query BattlEye ban status."""
    rid = await name_to_rid(name)
    result = await check_battleye_by_rid(rid)
    result["name"] = name
    return result
