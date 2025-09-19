from __future__ import annotations
import argparse
from .streaming import ThreadingTCPServer, NDJSONHandler

def main():
    ap = argparse.ArgumentParser(description="diagfw NDJSON collector")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9901)
    args = ap.parse_args()
    print(f"[collector] listening on {args.host}:{args.port}, writing to ./streams/")
    srv = ThreadingTCPServer((args.host, args.port), NDJSONHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
