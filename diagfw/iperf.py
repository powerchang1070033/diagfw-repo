from __future__ import annotations
from typing import Optional, List, Dict, Any
import threading, time, pathlib

from .model import test, DUT, TestResult
from .transport import Transport, LocalTransport
from .streaming import StreamMux, StreamSink

def _run_and_store(transport: Transport, cmd: str, container: dict, timeout: int|None=None):
    po = transport.run(cmd, timeout=timeout)
    container["result"] = po

def _dut_ip(dut: DUT) -> str:
    return str(dut.meta.get("iperf_ip") or dut.meta.get("ip") or dut.host)

def run_iperf_host_dut(
    dut: DUT,
    server_ip_for_dut: str,
    *,
    port: int = 5201,
    duration: int = 5,
    parallel: int = 1,
    uart_port: str | None = None,
    stream_sink: Optional[StreamSink] = None,
) -> TestResult:
    # local server in thread
    t_local = LocalTransport()
    server_cmd = f"iperf3 -s -1 -J -p {port}"
    server_box = {}
    server_th = threading.Thread(
        target=_run_and_store, args=(t_local, server_cmd, server_box, duration+30), daemon=True
    )
    server_th.start(); time.sleep(0.4)

    # client on DUT
    spec = (
        test("iperf-host-dut")
        .affects("net")
        .cmd(f"iperf3 -c {server_ip_for_dut} -p {port} -J -t {duration} -P {parallel} | tee iperf_client.json",
             timeout=duration+20)
        .version("iperf3 --version || true")
        .artifact("iperf_client.json")
    )
    if uart_port:
        spec.uart(uart_port)

    res = (spec @ dut).run(stream_sink=stream_sink)

    # attach server side result
    server_th.join(timeout=duration+40)
    out_dir = pathlib.Path(res.stdout_path).parent
    srv_json_path = out_dir / "iperf_server.json"
    srv_po = server_box.get("result")
    if srv_po:
        content = (srv_po.stdout or srv_po.stderr or "")
        srv_json_path.write_text(content, encoding="utf-8", errors="replace")
        res.artifacts["host_iperf_server"] = {
            "path": str(srv_json_path), "exists": True, "preview": content[:2000]
        }
        mux = StreamMux(stream_sink, res.run_id, res.test_name, dut)
        for ln in content.splitlines(True):
            mux.emit_text("host_iperf_server", ln)
    else:
        res.artifacts["host_iperf_server"] = {
            "path": str(srv_json_path), "exists": False, "error": "server thread no result"
        }
    return res

def run_iperf_mesh(
    duts: List[DUT],
    *,
    port: int = 5201,
    duration: int = 5,
    parallel: int = 1,
    stream_sink: Optional[StreamSink] = None,
) -> List[TestResult]:
    results: List[TestResult] = []

    for i in range(len(duts)):
        for j in range(i+1, len(duts)):
            server_dut = duts[i]
            client_dut = duts[j]
            server_ip = _dut_ip(server_dut)

            t_server = server_dut.get_transport()
            srv_box = {}
            srv_cmd = f"iperf3 -s -1 -J -p {port}"
            srv_th = threading.Thread(
                target=_run_and_store, args=(t_server, srv_cmd, srv_box, duration+30), daemon=True
            )
            srv_th.start(); time.sleep(0.5)

            pair_name = f"iperf-{client_dut.host}-to-{server_dut.host}"
            spec = (
                test(pair_name)
                .affects("net")
                .cmd(f"iperf3 -c {server_ip} -p {port} -J -t {duration} -P {parallel} | tee iperf_client.json",
                     timeout=duration+20)
                .version("iperf3 --version || true")
                .artifact("iperf_client.json")
            )
            res = (spec @ client_dut).run(stream_sink=stream_sink)

            srv_th.join(timeout=duration+40)
            out_dir = pathlib.Path(res.stdout_path).parent
            srv_json_path = out_dir / "iperf_server.json"
            srv_po = srv_box.get("result")
            if srv_po:
                content = (srv_po.stdout or srv_po.stderr or "")
                srv_json_path.write_text(content, encoding="utf-8", errors="replace")
                res.artifacts["server_iperf"] = {
                    "path": str(srv_json_path), "exists": True, "server": server_dut.host,
                    "preview": content[:2000]
                }
                mux = StreamMux(stream_sink, res.run_id, res.test_name, client_dut)
                for ln in content.splitlines(True):
                    mux.emit_text("server_iperf", ln, meta={"server": server_dut.host})
            else:
                res.artifacts["server_iperf"] = {
                    "path": str(srv_json_path), "exists": False, "server": server_dut.host,
                    "error": "server no result"
                }

            results.append(res)

    return results
