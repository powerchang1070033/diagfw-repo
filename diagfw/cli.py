from __future__ import annotations
import argparse, shutil, pathlib
from .model import test, DUT
from .streaming import TCPSink
from .collector import main as collector_main

def main():
    ap = argparse.ArgumentParser(description="diagfw CLI")
    ap.add_argument("--serve", action="store_true", help="run NDJSON collector server")
    ap.add_argument("--demo", action="store_true", help="run local demo (lspci or uname)")
    ap.add_argument("--collector-host", default="127.0.0.1")
    ap.add_argument("--collector-port", type=int, default=9901)
    args = ap.parse_args()

    if args.serve:
        collector_main()
        return

    if args.demo:
        sink = TCPSink(args.collector_host, args.collector_port)
        DUT_LOCAL = DUT(host="localhost", transport="local")
        cmd = "lspci -vv" if shutil.which("lspci") else "uname -a"
        res = (test("demo").affects("sys").cmd(cmd + " | tee demo.log", timeout=10).artifact("demo.log") @ DUT_LOCAL).run(stream_sink=sink)
        print(res.to_json(indent=2))
        return

    ap.print_help()

if __name__ == "__main__":
    main()
