"""Klien MCP (streamable HTTP): menyambungkan meetrec ke server MCP milik project lain.

Server tetap dijalankan oleh project pemiliknya (di sini: `python -m mcp_server` di
ultraman-transkrip, `http://127.0.0.1:8100/mcp`) — meetrec hanya menyambung, tidak menyalakan
prosesnya. Satu sesi MCP hidup di belakang satu task asyncio yang panjang, karena MCP memakai
cancel-scope anyio: masuk dan keluar context harus di task yang sama, kalau tidak penutupannya
melempar "Attempted to exit cancel scope in a different task".

Semua state koneksi (url, waktu sambung, daftar tool) dicatat di ~/.meetrec/mcp/<nama>.json —
itu yang dihapus oleh `restart()`/`clear_cache()`; data transkrip tidak pernah disentuh.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / ".meetrec" / "mcp"
DEFAULT_URL = "http://127.0.0.1:8100/mcp"


class McpError(RuntimeError):
    """Kesalahan yang teksnya sudah layak ditampilkan ke pengguna."""


class Connection:
    """One MCP server: connect / disconnect / restart, plus the tools it offers."""

    def __init__(self, name: str, url: str = DEFAULT_URL, key: str = ""):
        self.name, self.url, self.key = name, url, key  # key kosong = MCP publik tanpa autentikasi
        self.state = "idle"          # idle | connecting | connected
        self.server: dict = {}       # {"name":..., "version":...} from the handshake
        self.tools: list[dict] = []  # [{"name":..., "description":...}]
        self._loop = asyncio.new_event_loop()
        self._q: asyncio.Queue = asyncio.Queue()  # created here: connect() may run before _serve() starts
        self._stack: Any = None
        self._session: Any = None
        threading.Thread(target=self._loop.run_forever, daemon=True, name=f"meetrec-mcp-{name}").start()
        self._task = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        # exit: cancel the worker, then stop the loop. A full close() would talk to the server on the way out.
        atexit.register(self._stop_loop)

    # --- public ---------------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.state == "connected"

    @property
    def cache_path(self) -> Path:
        return STATE_DIR / f"{self.name}.json"

    def connect(self, timeout: float = 30.0) -> dict:
        self.state = "connecting"
        try:
            res = self._call("connect", timeout)
        except McpError:
            self.state = "idle"
            raise
        except Exception as e:
            self.state = "idle"
            raise McpError(_explain(e, self.url)) from None
        return res

    def disconnect(self, timeout: float = 15.0) -> None:
        try:
            self._call("disconnect", timeout)
        finally:
            self.state = "idle"

    def restart(self, timeout: float = 30.0) -> dict:
        """Putus, hapus state/cache koneksi, lalu sambung lagi."""
        self.disconnect()
        clear_cache(self.name)
        return self.connect(timeout)

    def close(self) -> None:
        """Disconnect and stop this connection's asyncio thread (window closing, app exit)."""
        try:
            self.disconnect()
        finally:
            self._stop_loop()

    def _stop_loop(self) -> None:
        try:
            if not self._task.done():
                self._task.cancel()
                time.sleep(0.05)  # let the cancellation land before the loop goes away
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:  # already closed at exit
            pass

    # --- the one task that owns the MCP context --------------------------------------------------
    async def _serve(self) -> None:
        while True:
            op, fut = await self._q.get()
            if fut.cancelled():
                continue
            try:
                res = await getattr(self, f"_op_{op}")()
            except BaseException as e:  # noqa: BLE001 — hand everything back to the caller
                fut.set_exception(e)
            else:
                fut.set_result(res)

    def _call(self, op: str, timeout: float) -> Any:
        fut: Future = Future()
        self._loop.call_soon_threadsafe(self._q.put_nowait, (op, fut))
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            raise McpError(f"{op} tidak selesai dalam {timeout:.0f} detik") from None

    async def _op_connect(self) -> dict:
        await self._op_disconnect()
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        stack = AsyncExitStack()
        try:
            kwargs = {}
            if self.key:
                # bearer token: only sent when set, so a public MCP keeps working with no header at all
                client = create_mcp_http_client(headers={"Authorization": f"Bearer {self.key}"})
                stack.push_async_callback(client.aclose)  # exits AFTER the transport, hence registered first
                kwargs["http_client"] = client
            read, write = await stack.enter_async_context(streamable_http_client(self.url, **kwargs))
            session = await stack.enter_async_context(ClientSession(read, write))
            init = await session.initialize()
            tools = (await session.list_tools()).tools
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._session = stack, session
        info = getattr(init, "server_info", None)
        self.server = {"name": getattr(info, "name", "") or "", "version": getattr(info, "version", "") or "",
                       "protocol": getattr(init, "protocol_version", "") or ""}
        self.tools = [{"name": t.name, "description": (t.description or "").strip()} for t in tools]
        self.state = "connected"
        self._save()
        return {"server": self.server, "tools": self.tools}

    async def _op_disconnect(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        self.state = "idle"
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:  # noqa: BLE001 — a dead server already closed its side
                pass

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(
                {"url": self.url, "connected_at": time.time(), "server": self.server, "tools": self.tools},
                ensure_ascii=False, indent=1))
        except OSError:
            pass  # cache is a convenience; a failed write must not break a working connection


def clear_cache(name: str) -> int:
    """Delete this connection's cached state. Returns how many files went away."""
    removed = 0
    p = STATE_DIR / f"{name}.json"
    if p.exists():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _explain(e: BaseException, url: str) -> str:
    root = _root_cause(e)
    text = str(root) or root.__class__.__name__
    low = text.lower()
    if "connect" in low or "refused" in low or "unreachable" in low or "all connection attempts" in low:
        return f"server MCP tidak bisa dihubungi di {url} — jalankan dulu servernya, lalu coba lagi"
    if "401" in text or "403" in text or "unauthorized" in low:
        return f"server MCP menolak ({text})"
    if "404" in text:
        return f"{url} bukan endpoint MCP (404). Cek alamat/port-nya"
    return text


def _root_cause(e: BaseException) -> BaseException:
    """anyio/httpx bury the real error in an ExceptionGroup ("unhandled errors in a TaskGroup")."""
    seen = 0
    while seen < 10:
        subs = getattr(e, "exceptions", None)
        if not subs:
            return e
        e, seen = subs[0], seen + 1
    return e
