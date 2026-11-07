"""Stdlib-only stdio -> Streamable HTTP bridge for trusted local MCP clients.

No request rewriting, automatic retries, GET push streams, or resumption.
"""
from __future__ import annotations

import argparse
import json
import stat
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_MESSAGE = 16 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_config(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError("Config must be a single owner-only regular file")
    config = json.loads(path.read_text(encoding="utf-8"))
    url = urlsplit(config["url"])
    try:
        port = url.port
    except ValueError as error:
        raise ValueError("Invalid MCP endpoint") from error
    local_http = url.scheme == "http" and url.hostname == "127.0.0.1"
    remote_https = url.scheme == "https" and url.hostname not in {None, "127.0.0.1"}
    if (
        not (local_http or remote_https)
        or (local_http and port is None)
        or url.path != "/mcp"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("MCP endpoint must be loopback HTTP or remote HTTPS at /mcp")
    if not isinstance(config.get("token"), str) or not config["token"]:
        raise ValueError("Missing MCP token")
    return config


def messages(response):
    """Decode bounded JSON or SSE, including multiline data and comments."""
    if response.headers.get_content_type() == "application/json":
        data = response.read(MAX_MESSAGE + 1)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Oversized response")
        if data:
            yield json.loads(data)
        return
    if response.headers.get_content_type() != "text/event-stream":
        raise ValueError("Unexpected response type")
    parts, size = [], 0
    while True:
        line = response.readline(MAX_MESSAGE + 1)
        if len(line) > MAX_MESSAGE:
            raise ValueError("Oversized SSE line")
        if not line or line in (b"\n", b"\r\n"):
            if parts:
                yield json.loads(b"\n".join(parts))
            parts, size = [], 0
            if not line:
                return
        elif line.startswith(b"data:"):
            value = line[5:].rstrip(b"\r\n")
            if value.startswith(b" "):
                value = value[1:]
            parts.append(value)
            size += len(value)
            if size > MAX_MESSAGE:
                raise ValueError("Oversized SSE event")


class Bridge:
    def __init__(self, config: dict, output=None):
        self.config = config
        self.output = output or sys.stdout
        self.lock = threading.Lock()
        self.session = None
        self.protocol = None
        self.slots = threading.BoundedSemaphore(16)

    def emit(self, message: dict) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("Invalid JSON-RPC response")
        with self.lock:
            self.output.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.output.flush()

    def request(self, method: str, data: bytes | None = None):
        headers = {
            "Authorization": "Bearer " + self.config["token"],
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        return build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(self.config["url"], data=data, headers=headers, method=method),
            timeout=120,
        )

    def forward(self, message: dict) -> None:
        try:
            with self.request("POST", json.dumps(message).encode("utf-8")) as response:
                if message.get("method") == "initialize":
                    self.session = response.headers.get("Mcp-Session-Id")
                if response.status == 202:
                    if "method" in message and "id" in message:
                        raise ValueError("Request accepted without a response")
                    return
                answered = False
                for reply in messages(response):
                    if message.get("method") == "initialize" and "result" in reply:
                        self.protocol = reply["result"]["protocolVersion"]
                    self.emit(reply)
                    if "id" in message and reply.get("id") == message["id"] and ("result" in reply or "error" in reply):
                        answered = True
                if "method" in message and "id" in message and not answered:
                    raise ValueError("Stream ended before response")
        except Exception as error:
            label = "HTTP " + str(error.code) if isinstance(error, HTTPError) else type(error).__name__
            if isinstance(error, HTTPError):
                error.close()
            print("MCP transport failed: " + label, file=sys.stderr)
            if "method" in message and "id" in message:
                self.emit({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32000,
                        "message": "MCP transport failed (" + label + "); request was not retried",
                    },
                })

    def worker(self, message: dict) -> None:
        try:
            self.forward(message)
        finally:
            self.slots.release()

    def run(self) -> None:
        workers = []
        try:
            while True:
                line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE:
                    raise ValueError("Oversized input")
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("Invalid JSON-RPC input")
                if message.get("method") in ("initialize", "notifications/initialized"):
                    self.forward(message)
                elif self.slots.acquire(blocking=False):
                    workers = [worker for worker in workers if worker.is_alive()]
                    worker = threading.Thread(target=self.worker, args=(message,))
                    workers.append(worker)
                    worker.start()
                elif "method" not in message or "id" not in message:
                    self.forward(message)
                else:
                    self.emit({
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32000, "message": "MCP connection concurrency limit reached"},
                    })
        finally:
            for worker in workers:
                worker.join()
            if self.session:
                try:
                    with self.request("DELETE"):
                        pass
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Stdio to Streamable HTTP MCP bridge")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    Bridge(load_config(args.config)).run()


if __name__ == "__main__":
    main()
