"""CLI for the server, safe event planning and durable job inspection."""

import argparse
from contextlib import ExitStack
import fcntl
import json
import logging
from pathlib import Path

from .config import Config
from .engine import Engine
from .server import serve
from .sonarr import Sonarr
from .state import State


def main():
    parser = argparse.ArgumentParser(description="Anime Sidecar Manager")
    parser.add_argument("--config", type=Path, default=Path("/config/config.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    event = sub.add_parser("event")
    event.add_argument("payload", type=Path)
    event.add_argument(
        "--apply", action="store_true", help="Apply only if config dry_run=false too"
    )
    sub.add_parser("jobs")
    retry = sub.add_parser("retry")
    retry.add_argument("id", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.load(args.config)
    if args.command == "event" and not args.apply:
        config.dry_run = True
    config.database.parent.mkdir(parents=True, exist_ok=True)
    # Only media writers need the lock; queue inspection/retry may run with the server.
    with ExitStack() as stack:
        if args.command in {"serve", "event"}:
            lock = stack.enter_context(config.database.with_suffix(".lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "serve":
            serve(config)
            return
        state = State(config.database)
        try:
            if args.command == "event":
                report = Engine(config, Sonarr(config), state).process(
                    json.loads(args.payload.read_text())
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif args.command == "jobs":
                print(
                    json.dumps(
                        [
                            dict(row)
                            for row in state.db.execute(
                                "SELECT id,status,attempts,error FROM jobs ORDER BY id DESC LIMIT 100"
                            )
                        ],
                        indent=2,
                    )
                )
            elif args.command == "retry":
                state.db.execute(
                    "UPDATE jobs SET status='queued',attempts=0,next_attempt=0,error=NULL WHERE id=? AND status='failed'",
                    (args.id,),
                )
                state.db.commit()
        finally:
            state.close()


if __name__ == "__main__":
    main()
