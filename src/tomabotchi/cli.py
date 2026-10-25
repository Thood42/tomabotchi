"""tomabotchi CLI: `tomabotchi serve`."""
from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tomabotchi")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from . import config
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run("tomabotchi.web:app", host=args.host, port=args.port or config.load().port,
                    proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
