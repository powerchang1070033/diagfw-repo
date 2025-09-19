from __future__ import annotations
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid, json, socket, base64, pathlib, socketserver

class StreamSink:
    def open(self): ...
    def close(self): ...
    def send(self, rec: dict): ...  # one NDJSON record

class NullSink(StreamSink):
    def send(self, rec: dict): pass

class TCPSink(StreamSink):
    def __init__(self, host: str = "127.0.0.1", port: int = 9901, reconnect: bool = True):
        self.host = host; self.port = port
        self.reconnect = reconnect
        self.sock: socket.socket|None = None

    def open(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=3)

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None

    def send(self, rec: dict):
        data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8", "replace")
        try:
            if not self.sock:
                self.open()
            assert self.sock is not None
            self.sock.sendall(data)
        except Exception:
            if self.reconnect:
                try:
                    self.open(); assert self.sock is not None
                    self.sock.sendall(data)
                except Exception:
                    pass

class StreamMux:
    """Assigns stream_id per source name and emits NDJSON records to sink."""
    def __init__(self, sink: Optional[StreamSink], run_id: str, test_name: str, dut):
        self.sink = sink
        self.run_id = run_id
        self.test_name = test_name
        self.dut = dut
        self._seq = 0
        self._streams: Dict[str, str] = {}

    def stream_id(self, name: str) -> str:
        sid = self._streams.get(name)
        if not sid:
            sid = f"{name}-{uuid.uuid4().hex[:8]}"
            self._streams[name] = sid
        return sid

    def emit_text(self, name: str, text: str, meta: dict|None = None):
        if not self.sink:
            return
        self._seq += 1
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": self._seq,
            "run_id": self.run_id,
            "test": self.test_name,
            "dut": getattr(self.dut, "host", "unknown"),
            "source": name,
            "stream_id": self.stream_id(name),
            "text": text,
            "encoding": "utf-8",
        }
        if meta:
            rec["meta"] = meta
        try:
            self.sink.send(rec)
        except Exception:
            pass

    def emit_blob(self, name: str, data: bytes, meta: dict|None = None):
        if not self.sink:
            return
        self._seq += 1
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": self._seq,
            "run_id": self.run_id,
            "test": self.test_name,
            "dut": getattr(self.dut, "host", "unknown"),
            "source": name,
            "stream_id": self.stream_id(name),
            "blob_b64": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
        }
        if meta:
            rec["meta"] = meta
        try:
            self.sink.send(rec)
        except Exception:
            pass

# Collector server (central aggregator)
class NDJSONHandler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline()
            if not line:
                break
            try:
                obj = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            run = obj.get("run_id", "unknown")
            test = obj.get("test", "unknown")
            sid = obj.get("stream_id", "stream")
            src = obj.get("source", "src")
            dirp = pathlib.Path("streams")/run/test
            dirp.mkdir(parents=True, exist_ok=True)
            # raw NDJSON per stream
            with open(dirp / f"{sid}_{src}.ndjson", "ab") as f:
                f.write(line)
            # human log per source
            if "text" in obj:
                with open(dirp / f"{src}.log", "a", encoding="utf-8", errors="replace") as f2:
                    f2.write(obj["text"])  # append

class ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

def serve_streams(host: str = "0.0.0.0", port: int = 9901) -> ThreadingTCPServer:
    srv = ThreadingTCPServer((host, port), NDJSONHandler)
    import threading
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
