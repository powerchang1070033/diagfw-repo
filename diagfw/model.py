from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
import json, uuid, pathlib

from .transport import Transport, LocalTransport, SSHTransport
from .uart import UartSpec, UartTap
from .streaming import StreamMux, StreamSink

@dataclass
class CommandSpec:
    cmd: str
    stdin: Optional[str] = None
    timeout: Optional[int] = None
    env: Dict[str,str] = field(default_factory=dict)
    cwd: Optional[str] = None
    version_cmd: Optional[str] = None
    declared_artifacts: List[str] = field(default_factory=list)  # paths on DUT

@dataclass
class DUT:
    host: str = "localhost"
    transport: str = "local"          # "local" | "ssh"
    user: Optional[str] = None
    ssh_opts: Optional[List[str]] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def get_transport(self) -> Transport:
        if self.transport == "ssh":
            return SSHTransport(self.host, user=self.user, ssh_opts=self.ssh_opts)
        return LocalTransport()

@dataclass
class TestResult:
    run_id: str
    test_name: str
    affects: List[str]
    started_at: str
    ended_at: str
    rc: int
    status: str                 # pass|fail|error
    stdout_path: str
    stderr_path: str
    version: Optional[str]
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dut: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_json(self, **kw) -> str:
        kw.setdefault("ensure_ascii", False)
        return json.dumps(asdict(self), **kw)

class TestSpec:
    """User-facing builder. Supports: test("pcie").affects(...).cmd(...).artifact(...).version(...).uart(...)."""
    def __init__(self, name: str):
        self.name = name
        self._affects: List[str] = []
        self._cmd: Optional[CommandSpec] = None
        self._uart: Optional[UartSpec] = None

    def affects(self, items: List[str] | str) -> "TestSpec":
        if isinstance(items, str): items = [items]
        self._affects = list(items)
        return self

    def cmd(self, command: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
            env: Optional[Dict[str,str]]=None, cwd: Optional[str]=None) -> "TestSpec":
        self._ensure_cmd()
        self._cmd.cmd = command
        self._cmd.stdin = stdin
        self._cmd.timeout = timeout
        self._cmd.env = env or {}
        self._cmd.cwd = cwd
        return self

    def version(self, version_cmd: str) -> "TestSpec":
        self._ensure_cmd()
        self._cmd.version_cmd = version_cmd
        return self

    def artifact(self, path: str) -> "TestSpec":
        self._ensure_cmd()
        self._cmd.declared_artifacts.append(path)
        return self

    def uart(self, port: str, *, baudrate: int = 115200, bytesize: int = 8, parity: str = "N",
             stopbits: float = 1, read_timeout: float = 0.1, encoding: str = "utf-8",
             linger: float = 0.5, max_preview: int = 2000) -> "TestSpec":
        self._uart = UartSpec(port=port, baudrate=baudrate, bytesize=bytesize, parity=parity,
                              stopbits=stopbits, read_timeout=read_timeout, encoding=encoding,
                              linger=linger, max_preview=max_preview)
        return self

    def _ensure_cmd(self):
        if self._cmd is None:
            self._cmd = CommandSpec(cmd="")

    def __matmul__(self, dut: DUT) -> "BoundTest":
        if self._cmd is None or not self._cmd.cmd:
            raise ValueError("Command not specified. Use .cmd('...') before binding to a DUT.")
        return BoundTest(self, dut)

class BoundTest:
    def __init__(self, spec: TestSpec, dut: DUT):
        self.spec = spec
        self.dut = dut

    def run(self, *, artifacts_root: str = "artifacts", stream_sink: Optional[StreamSink]=None) -> TestResult:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        t = self.dut.get_transport()

        out_dir = pathlib.Path(artifacts_root) / run_id / self.spec.name
        out_dir.mkdir(parents=True, exist_ok=True)
        stdout_file = out_dir / "stdout.log"
        stderr_file = out_dir / "stderr.log"

        mux = StreamMux(stream_sink, run_id, self.spec.name, self.dut)

        # Optional UART capture (stream + file)
        uart_meta: Dict[str, Any] = {}
        uart_tap: Optional[UartTap] = None
        if self.spec._uart:
            uart_path = out_dir / "uart.log"
            def _uart_emit(chunk: bytes):
                try:
                    mux.emit_text("uart", chunk.decode(self.spec._uart.encoding, errors="replace"))
                except Exception:
                    from .streaming import base64
                    mux.emit_blob("uart", chunk)
            uart_tap = UartTap(self.spec._uart, uart_path, on_chunk=_uart_emit)
            if uart_tap.start():
                uart_meta.update({
                    "path": str(uart_path),
                    "port": self.spec._uart.port,
                    "baudrate": self.spec._uart.baudrate,
                })
            else:
                uart_meta.update({
                    "path": str(uart_path),
                    "port": self.spec._uart.port,
                    "baudrate": self.spec._uart.baudrate,
                    "exists": False,
                    "error": uart_tap.error or "unknown",
                })
                uart_tap = None

        # Run version command (best‑effort)
        version_text = None
        if self.spec._cmd.version_cmd:
            vout = t.run(self.spec._cmd.version_cmd, timeout=10)
            version_text = (vout.stdout or vout.stderr or "").strip()

        # Execute main command (streaming to files + sink)
        stdout_fp = open(stdout_file, "w", encoding="utf-8", errors="replace")
        stderr_fp = open(stderr_file, "w", encoding="utf-8", errors="replace")

        def on_out(line: str):
            stdout_fp.write(line); stdout_fp.flush()
            mux.emit_text("stdout", line)
        def on_err(line: str):
            stderr_fp.write(line); stderr_fp.flush()
            mux.emit_text("stderr", line)

        try:
            rc = t.stream(self.spec._cmd.cmd,
                          stdin=self.spec._cmd.stdin,
                          timeout=self.spec._cmd.timeout,
                          cwd=self.spec._cmd.cwd,
                          env=self.spec._cmd.env,
                          on_stdout=on_out, on_stderr=on_err)
            status = "pass" if rc == 0 else "fail"
        except Exception as e:
            rc = 127
            status = "error"
            mux.emit_text("stderr", f"ERROR: {e!r}\n")
        finally:
            try: stdout_fp.close()
            except Exception: pass
            try: stderr_fp.close()
            except Exception: pass

        # Stop UART capture (with linger)
        if uart_tap:
            uart_tap.stop(self.spec._uart.linger)
            uart_preview = uart_tap.preview_text(self.spec._uart.max_preview, self.spec._uart.encoding)
            uart_meta.update({
                "exists": True,
                "bytes_captured": uart_tap.bytes_captured,
                "preview": uart_preview if uart_preview is not None else None
            })

        # Collect declared artifacts (best‑effort snapshot + streaming)
        artifacts_meta: Dict[str, Dict[str, Any]] = {}
        for p in self.spec._cmd.declared_artifacts:
            content = t.read_text(p)
            meta = {"path": p, "exists": content is not None, "preview": None}
            if content is not None:
                preview = content if len(content) <= 2000 else content[:2000] + "\n...<truncated>..."
                (out_dir / pathlib.Path(p).name).write_text(content, encoding="utf-8", errors="replace")
                meta["preview"] = preview
                for ln in content.splitlines(True):
                    mux.emit_text(f"artifact:{pathlib.Path(p).name}", ln)
            artifacts_meta[p] = meta

        if self.spec._uart:
            artifacts_meta["uart"] = uart_meta

        ended_at = datetime.now(timezone.utc)
        msg = f"'%s' rc=%s; affects=%s; cmd=%r" % (self.spec.name, rc, self.spec._affects, self.spec._cmd.cmd)

        return TestResult(
            run_id=run_id,
            test_name=self.spec.name,
            affects=self.spec._affects,
            started_at=started_at.isoformat(),
            ended_at=ended_at.isoformat(),
            rc=rc,
            status=status,
            stdout_path=str(stdout_file),
            stderr_path=str(stderr_file),
            version=version_text,
            artifacts=artifacts_meta,
            dut={"host": self.dut.host, "transport": self.dut.transport, **(self.dut.meta or {})},
            message=msg,
        )

def test(name: str) -> TestSpec:
    return TestSpec(name)
