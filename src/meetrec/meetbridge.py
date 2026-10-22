"""Receives "who speaks when" from the meetrec Chrome extension (Google Meet) on this computer only.

- Listens on 127.0.0.1 (never reachable from the network).
- Every request needs the header X-Meetrec-Token (the code shown in Pengaturan).
- No CORS headers on purpose: a web page cannot call this, only the extension's service worker can.
- Events are kept only while a recording runs (begin() .. end()); otherwise they are dropped.
"""
from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 47831
MAX_BODY = 1_000_000
MAX_EVENTS_PER_REQUEST = 2000
MERGE_GAP = 0.8  # seconds: two intervals of the same person closer than this are one turn


def clean_event(raw: dict) -> dict | None:
    """Keep only the fields we know, with sane types. Anything else is dropped."""
    try:
        t = float(raw["t"])
        state = raw["state"]
        name = str(raw["name"]).strip()[:100]
        source = raw.get("source", "tile")
    except (KeyError, TypeError, ValueError):
        return None
    if state not in ("start", "stop") or not name or source not in ("tile", "caption") or t <= 0:
        return None
    return {"t": t, "state": state, "name": name, "id": str(raw.get("id", name))[:100],
            "self": bool(raw.get("self", False)), "source": source}


def build_intervals(events: list[dict], t0_wall: float, end_wall: float) -> list[dict]:
    """start/stop events -> [{name, start, end, sources, self}] in seconds since the recording's t0.

    A start without stop (the page closed, the recording ended) is closed at end_wall.
    A stop without start (the extension joined late) is ignored. Same name from several signals is merged."""
    open_: dict[tuple, dict] = {}
    raw: list[dict] = []
    for e in sorted(events, key=lambda e: e["t"]):
        key = (e["source"], e["id"])
        ts = e["t"] / 1000.0
        if e["state"] == "start":
            open_.setdefault(key, {"name": e["name"], "start": ts, "source": e["source"], "self": e["self"]})
        elif key in open_:
            o = open_.pop(key)
            raw.append({**o, "end": max(ts, o["start"])})
    for o in open_.values():
        raw.append({**o, "end": max(end_wall, o["start"])})

    by_name: dict[str, list[dict]] = {}
    for o in raw:
        s, e = o["start"] - t0_wall, o["end"] - t0_wall
        if e <= 0:
            continue
        by_name.setdefault(o["name"], []).append({"start": max(0.0, s), "end": e, "source": o["source"], "self": o["self"]})

    out: list[dict] = []
    for name, ivs in by_name.items():
        ivs.sort(key=lambda i: i["start"])
        cur = None
        for i in ivs:
            if cur and i["start"] - cur["end"] <= MERGE_GAP:
                cur["end"] = max(cur["end"], i["end"])
                cur["sources"].add(i["source"])
                cur["self"] = cur["self"] or i["self"]
            else:
                if cur:
                    out.append(cur)
                cur = {"name": name, "start": i["start"], "end": i["end"], "sources": {i["source"]}, "self": i["self"]}
        if cur:
            out.append(cur)
    for o in out:
        o["sources"] = sorted(o["sources"])
        o["start"], o["end"] = round(o["start"], 2), round(o["end"], 2)
    out.sort(key=lambda o: (o["start"], o["end"]))
    return out


class MeetBridge:
    def __init__(self, token: str, port: int = DEFAULT_PORT):
        self.token, self.port = token, port
        self.error = ""
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()
        self._recording = False
        self._t0_wall = 0.0
        self._events: list[dict] = []
        self._meeting = ""
        self.last_seen = 0.0  # wall time of the last accepted request

    # --- server ----------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        if self._server:
            return True
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _authed(self) -> bool:
                got = self.headers.get("X-Meetrec-Token", "")
                if hmac.compare_digest(got.encode(), bridge.token.encode()):
                    return True
                self._send(401, {"ok": False, "error": "bad token"})
                return False

            def do_GET(self):
                if self.path != "/v1/hello":
                    return self._send(404, {"ok": False})
                if not self._authed():
                    return
                bridge.last_seen = time.time()
                self._send(200, {"ok": True, "app": "meetrec", "recording": bridge.recording, "events": bridge.count})

            def do_POST(self):
                if self.path != "/v1/events":
                    return self._send(404, {"ok": False})
                if not self._authed():
                    return
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    return self._send(413, {"ok": False, "error": "body too large"})
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    events = body.get("events", [])
                    if not isinstance(events, list):
                        raise ValueError("events must be a list")
                except (ValueError, AttributeError):
                    return self._send(400, {"ok": False, "error": "bad json"})
                bridge.last_seen = time.time()
                accepted = bridge.add(str(body.get("meeting", ""))[:60], events[:MAX_EVENTS_PER_REQUEST])
                self._send(200, {"ok": True, "recording": bridge.recording, "accepted": accepted})

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            self.error = f"port {self.port}: {e.strerror or e}"
            self._server = None
            return False
        self.port = self._server.server_address[1]  # real port when 0 was asked
        threading.Thread(target=self._server.serve_forever, daemon=True, name="meet-bridge").start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # --- recording window ------------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def begin(self, t0_wall: float) -> None:
        with self._lock:
            self._events, self._meeting = [], ""
            self._t0_wall, self._recording = t0_wall, True

    def add(self, meeting: str, events: list) -> int:
        if not self._recording:
            return 0
        clean = [c for c in (clean_event(e) for e in events if isinstance(e, dict)) if c]
        with self._lock:
            if self._recording:
                self._events.extend(clean)
                self._meeting = meeting or self._meeting
        return len(clean)

    def names(self) -> list[str]:
        with self._lock:
            return sorted({e["name"] for e in self._events if not e["self"]})

    def end(self) -> dict:
        """Stop collecting; returns everything needed to store next to the recording."""
        with self._lock:
            self._recording = False
            events, meeting, t0 = list(self._events), self._meeting, self._t0_wall
            self._events = []
        return {"meeting": meeting, "t0_wall": t0, "events": events,
                "intervals": build_intervals(events, t0, time.time())}
