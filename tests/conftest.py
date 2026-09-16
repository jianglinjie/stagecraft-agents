"""Shared fixtures: a real uvicorn server and an SSE reader for the HTTP tests.

httpx's ASGI transport buffers a whole response before returning it, which never
happens for an event stream, so the HTTP tests talk to a real server on a free port.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


def read_events(
    base: str,
    session_id: str,
    *,
    until: Callable[[list[dict[str, Any]]], bool],
    last_event_id: int | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Read SSE frames until ``until(events)`` holds. Relies on heartbeats to wake up."""
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    events: list[dict[str, Any]] = []
    frame: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    url = f"{base}/sessions/{session_id}/events"
    with (
        httpx.Client(timeout=timeout) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line and not line.startswith(":"):
                key, _, value = line.partition(": ")
                frame[key] = value
                continue
            if frame.get("event"):
                events.append(
                    {"id": int(frame["id"]), "event": frame["event"], **json.loads(frame["data"])}
                )
            frame = {}
            if until(events):
                return events
            if time.monotonic() > deadline:
                raise TimeoutError(f"condition not met; got {[e['event'] for e in events]}")
    return events


def wait_until(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.02)
