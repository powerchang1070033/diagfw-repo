from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Callable, List
import subprocess, shlex, time, contextlib
import socket
import os

@dataclass
class ProcOutput:
    rc: int
    stdout: str
    stderr: str

class Transport:
    def run(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
            cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None) -> ProcOutput:
        raise NotImplementedError

    def stream(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
               cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None,
               on_stdout: Optional[Callable[[str], None]] = None,
               on_stderr: Optional[Callable[[str], None]] = None) -> int:
        """Run command and incrementally deliver stdout/stderr as TEXT lines to callbacks.
        Return process exit code. Implementations should be best‑effort."""
        raise NotImplementedError

    def read_text(self, path: str, max_bytes: int = 64_000) -> Optional[str]:
        """Best‑effort to read a file's content from DUT. Return None if not readable."""
        raise NotImplementedError

class LocalTransport(Transport):
    def run(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
            cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None) -> ProcOutput:
        p = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout,
                           cwd=cwd, env=env, shell=True)
        return ProcOutput(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)

    def stream(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
               cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None,
               on_stdout: Optional[Callable[[str], None]] = None,
               on_stderr: Optional[Callable[[str], None]] = None) -> int:
        p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,
                             stdin=subprocess.PIPE if stdin else None,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        if stdin and p.stdin:
            try:
                p.stdin.write(stdin)
                p.stdin.close()
            except Exception:
                pass

        def reader(pipe, cb):
            try:
                for line in iter(pipe.readline, ""):
                    if cb:
                        cb(line)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        th_out = None
        th_err = None
        if p.stdout:
            import threading
            th_out = threading.Thread(target=reader, args=(p.stdout, on_stdout), daemon=True)
            th_out.start()
        if p.stderr:
            import threading
            th_err = threading.Thread(target=reader, args=(p.stderr, on_stderr), daemon=True)
            th_err.start()

        start = time.time()
        rc = None
        while True:
            rc = p.poll()
            if rc is not None:
                break
            if timeout and (time.time() - start) > timeout:
                with contextlib.suppress(Exception):
                    p.kill()
                rc = 124
                break
            time.sleep(0.05)
        if th_out: th_out.join(timeout=2)
        if th_err: th_err.join(timeout=2)
        return rc if rc is not None else (p.returncode or 0)

    def read_text(self, path: str, max_bytes: int = 64_000) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                data = f.read(max_bytes)
            return data.decode(errors="replace")
        except Exception:
            return None

class SSHTransport(Transport):
    def __init__(self, host: str, user: Optional[str] = None, ssh_opts: Optional[List[str]] = None):
        self.target = f"{user+'@' if user else ''}{host}"
        self.ssh_opts = ssh_opts or ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

    def _wrap_cmd(self, cmd: str, cwd: Optional[str], env: Optional[Dict[str,str]]) -> List[str]:
        env_prefix = ""
        if env:
            assigns = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            env_prefix = f"{assigns} "
        cd_prefix = f"cd {shlex.quote(cwd)} && " if cwd else ""
        wrapped = f"bash -lc {shlex.quote(env_prefix + cd_prefix + cmd)}"
        return ["ssh", *self.ssh_opts, self.target, wrapped]

    def run(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
            cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None) -> ProcOutput:
        ssh_cmd = self._wrap_cmd(cmd, cwd, env)
        p = subprocess.run(ssh_cmd, input=stdin, text=True, capture_output=True, timeout=timeout)
        return ProcOutput(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)

    def stream(self, cmd: str, *, stdin: Optional[str]=None, timeout: Optional[int]=None,
               cwd: Optional[str]=None, env: Optional[Dict[str,str]]=None,
               on_stdout: Optional[Callable[[str], None]] = None,
               on_stderr: Optional[Callable[[str], None]] = None) -> int:
        ssh_cmd = self._wrap_cmd(cmd, cwd, env)
        p = subprocess.Popen(ssh_cmd, stdin=subprocess.PIPE if stdin else None,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        if stdin and p.stdin:
            try:
                p.stdin.write(stdin)
                p.stdin.close()
            except Exception:
                pass

        def reader(pipe, cb):
            try:
                for line in iter(pipe.readline, ""):
                    if cb:
                        cb(line)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        import threading
        th_out = threading.Thread(target=reader, args=(p.stdout, on_stdout), daemon=True)
        th_err = threading.Thread(target=reader, args=(p.stderr, on_stderr), daemon=True)
        th_out.start(); th_err.start()

        start = time.time()
        rc = None
        while True:
            rc = p.poll()
            if rc is not None:
                break
            if timeout and (time.time() - start) > timeout:
                with contextlib.suppress(Exception):
                    p.kill()
                rc = 124
                break
            time.sleep(0.05)
        th_out.join(timeout=2); th_err.join(timeout=2)
        return rc if rc is not None else (p.returncode or 0)

    def read_text(self, path: str, max_bytes: int = 64_000) -> Optional[str]:
        try:
            remote = f"bash -lc {shlex.quote(f'head -c {max_bytes} {shlex.quote(path)}')}"
            ssh_cmd = ["ssh", *self.ssh_opts, self.target, remote]
            p = subprocess.run(ssh_cmd, text=True, capture_output=True, timeout=10)
            if p.returncode == 0:
                return p.stdout
        except Exception:
            pass
        return None
