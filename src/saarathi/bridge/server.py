"""Newline-delimited JSON-RPC 2.0 over stdin/stdout (protocol 1, ADR-0013).

Requests are handled one at a time on the main thread. A poller thread with its own read-only database connections
pushes ``events/appended`` notifications for subscribed projects. stdout carries protocol messages only; diagnostics
go to stderr. The bridge exits when stdin closes. Running drivers are separate processes and keep going.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import threading
import traceback
from pathlib import Path
from typing import IO, Any

from saarathi.bridge.service import PROTOCOL, BridgeService, error_payload, jsonable

POLL_SECONDS = 0.2
ERROR_CODES = {
    "invalid_params": -32602,
    "not_found": -32004,
    "project_busy": -32009,
    "invalid_state": -32010,
    "policy": -32011,
    "locked": -32012,
    "incompatible": -32013,
    "not_a_repo": -32014,
    "not_initialized": -32015,
    "internal": -32603,
}


class Bridge:
    def __init__(self, stdin: IO[str], stdout: IO[str]) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.service = BridgeService()
        self.methods = self.service.methods()
        self.methods["subscribe"] = self.subscribe
        self.methods["unsubscribe"] = self.unsubscribe
        self._write = threading.Lock()
        self._subs: dict[str, int] = {}  # project root -> last delivered seq
        self._subs_lock = threading.Lock()
        self._stop = threading.Event()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, default=str, separators=(",", ":"))
        with self._write:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    # ------------------------------------------------------------------ subscriptions

    def subscribe(self, project: str, since_seq: int | None = None) -> dict[str, Any]:
        p = self.service.project(project)
        root = str(p.root)
        latest = p.store.query("SELECT COALESCE(MAX(seq), 0) FROM events")[0][0]
        with self._subs_lock:
            self._subs[root] = int(latest if since_seq is None else since_seq)
        return {"project": root, "since_seq": self._subs[root], "latest_seq": int(latest)}

    def unsubscribe(self, project: str) -> dict[str, Any]:
        root = str(Path(project).expanduser().resolve())
        with self._subs_lock:
            removed = self._subs.pop(root, None) is not None
        return {"unsubscribed": removed}

    def _poll(self) -> None:
        connections: dict[str, sqlite3.Connection] = {}
        while not self._stop.wait(POLL_SECONDS):
            with self._subs_lock:
                subs = dict(self._subs)
            for root, since in subs.items():
                try:
                    conn = connections.get(root)
                    if conn is None:
                        uri = (Path(root) / ".saarathi" / "state.db").resolve().as_uri() + "?mode=ro"
                        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                        conn.row_factory = sqlite3.Row
                        connections[root] = conn
                    rows = conn.execute(
                        "SELECT seq, ts, actor, run_id, task_id, type, payload_json FROM events"
                        " WHERE seq > ? ORDER BY seq LIMIT 500",
                        (since,),
                    ).fetchall()
                except sqlite3.Error as exc:
                    print(f"bridge: poll failed for {root}: {exc}", file=sys.stderr)
                    continue
                if not rows:
                    continue
                events = [
                    {
                        "seq": r["seq"],
                        "ts": r["ts"],
                        "actor": r["actor"],
                        "run_id": r["run_id"],
                        "task_id": r["task_id"],
                        "type": r["type"],
                        "payload": json.loads(r["payload_json"]),
                    }
                    for r in rows
                ]
                with self._subs_lock:
                    if root in self._subs:
                        self._subs[root] = max(self._subs[root], events[-1]["seq"])
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "events/appended",
                        "params": {
                            "protocol": PROTOCOL,
                            "project": root,
                            "events": events,
                            "runs": sorted({e["run_id"] for e in events if e["run_id"]}),
                        },
                    }
                )
        for conn in connections.values():
            conn.close()

    # ------------------------------------------------------------------ requests

    def handle(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {exc}"}})
            return
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or not isinstance(params, dict):
            self.send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "invalid request"}})
            return
        handler = self.methods.get(method)
        if handler is None:
            self.send(
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
            )
            return
        try:
            inspect.signature(handler).bind(**params)
        except TypeError as exc:
            self._error(req_id, "invalid_params", f"{method}: {exc}", None)
            return
        try:
            result = handler(**params)
        except Exception as exc:
            kind, msg, hint = error_payload(exc)
            if kind == "internal":
                self._unexpected(req_id, exc)
                return
            self._error(req_id, kind, msg, hint)
            return
        if req_id is not None:
            self.send({"jsonrpc": "2.0", "id": req_id, "result": jsonable(result), "protocol": PROTOCOL})

    def _error(self, req_id: Any, kind: str, message: str, hint: str | None) -> None:
        if req_id is None:
            return
        self.send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "protocol": PROTOCOL,
                "error": {
                    "code": ERROR_CODES.get(kind, -32603),
                    "message": message,
                    "data": {"kind": kind, "hint": hint},
                },
            }
        )

    def _unexpected(self, req_id: Any, exc: Exception) -> None:
        traceback.print_exc(file=sys.stderr)
        self._error(req_id, "internal", f"{type(exc).__name__}: {exc}", "see the bridge log for details")

    def serve(self) -> int:
        poller = threading.Thread(target=self._poll, name="saarathi-bridge-poll", daemon=True)
        poller.start()
        try:
            for line in self.stdin:
                if line.strip():
                    self.handle(line)
        finally:
            self._stop.set()
            poller.join(timeout=2)
        return 0


def serve_stdio() -> int:
    # Line-buffered UTF-8 regardless of platform defaults; stdout is the protocol channel.
    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False, newline="\n")  # noqa: SIM115
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False, newline="\n")  # noqa: SIM115
    return Bridge(stdin, stdout).serve()
