from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Callable
import pathlib, threading, time

@dataclass
class UartSpec:
    port: str                   # e.g., "/dev/ttyUSB0" or "COM3"
    baudrate: int = 115200
    bytesize: int = 8           # 5/6/7/8
    parity: str = "N"           # 'N', 'E', 'O', 'M', 'S'
    stopbits: float = 1         # 1, 1.5, 2
    read_timeout: float = 0.1   # seconds
    encoding: str = "utf-8"
    linger: float = 0.5         # seconds to continue capturing after command ends
    max_preview: int = 2000     # bytes shown in preview

class UartTap:
    """Background UART capture to a file; optionally emits chunks to a callback."""
    def __init__(self, spec: UartSpec, outfile_path: pathlib.Path,
                 on_chunk: Optional[Callable[[bytes], None]] = None):
        self.spec = spec
        self.outfile_path = outfile_path
        self.stop_evt = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.bytes_captured = 0
        self.error: Optional[str] = None
        self._ser = None  # type: ignore
        self._on_chunk = on_chunk

    def _open_serial(self):
        try:
            import serial  # pyserial
        except Exception as e:
            self.error = f"pyserial not available: {e}"
            return False

        try:
            from serial import EIGHTBITS, SEVENBITS, SIXBITS, FIVEBITS
            from serial import PARITY_NONE, PARITY_EVEN, PARITY_ODD, PARITY_MARK, PARITY_SPACE
            from serial import STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO

            size_map = {8:EIGHTBITS,7:SEVENBITS,6:SIXBITS,5:FIVEBITS}
            parity_map = {"N":PARITY_NONE,"E":PARITY_EVEN,"O":PARITY_ODD,"M":PARITY_MARK,"S":PARITY_SPACE}
            stop_map = {1:STOPBITS_ONE,1.5:STOPBITS_ONE_POINT_FIVE,2:STOPBITS_TWO}

            self._ser = serial.Serial(
                port=self.spec.port,
                baudrate=self.spec.baudrate,
                bytesize=size_map.get(self.spec.bytesize, EIGHTBITS),
                parity=parity_map.get(self.spec.parity.upper(), PARITY_NONE),
                stopbits=stop_map.get(self.spec.stopbits, STOPBITS_ONE),
                timeout=self.spec.read_timeout
            )
            return True
        except Exception as e:
            self.error = f"open serial failed: {e}"
            return False

    def start(self) -> bool:
        if not self._open_serial():
            return False
        self.thread = threading.Thread(target=self._loop, name="UartTap", daemon=True)
        self.thread.start()
        return True

    def _loop(self):
        try:
            with open(self.outfile_path, "wb") as f:
                while not self.stop_evt.is_set():
                    try:
                        data = self._ser.read(4096)  # type: ignore
                        if data:
                            f.write(data); f.flush()
                            self.bytes_captured += len(data)
                            if self._on_chunk:
                                self._on_chunk(data)
                    except Exception as e:
                        self.error = f"read error: {e}"
                        break
        finally:
            try:
                if self._ser:
                    self._ser.close()
            except Exception:
                pass

    def stop(self, linger: float = 0.0):
        if linger > 0:
            time.sleep(linger)
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def preview_text(self, max_bytes: int, encoding: str) -> str|None:
        try:
            with open(self.outfile_path, "rb") as f:
                data = f.read(max_bytes)
            return data.decode(encoding=encoding, errors="replace")
        except Exception:
            return None
