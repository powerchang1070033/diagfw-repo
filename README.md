# diagfw

A tiny diagnostic framework with:
- Local/SSH command execution (streaming stdout/stderr)
- Optional UART side-channel capture (pyserial)
- NDJSON streaming to a central collector (TCP)
- iperf host↔DUT and multi‑DUT mesh helpers

## Quick start

### 1) Install (dev)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

### 2) Run collector
```bash
python -m diagfw.cli --serve
```

### 3) Run demo (streams logs to the collector)
```bash
python -m diagfw.cli --demo --collector-host 127.0.0.1 --collector-port 9901
```

Artifacts are saved under `./artifacts/<run_id>/<test_name>/`  
Streams are saved under `./streams/<run_id>/<test_name>/`

## Using in code
```python
from diagfw import DUT, TCPSink, run_iperf_host_dut
sink = TCPSink("127.0.0.1", 9901)
res = run_iperf_host_dut(
    dut=DUT(host="192.168.10.101", transport="ssh", user="root", meta={"ip":"192.168.10.101"}),
    server_ip_for_dut="192.168.10.10",
    duration=8,
    stream_sink=sink,
)
print(res.to_json(indent=2))
```

## Dev with VS Code
- Open folder, VS Code will suggest Recommended Extensions.
- Use **Run and Debug**: *Collector (NDJSON)* or *Demo (local)*.
- Tasks: `Run: Collector`, `Run: Demo`, `Test: pytest`.
- Optional: open in **Dev Container/Codespaces** for reproducible env.

## License
MIT
