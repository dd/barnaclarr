"""CLI for the server, safe event planning and durable job inspection."""

import argparse
from contextlib import ExitStack
import fcntl
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import os
from pathlib import Path

from .config import Config
from .engine import Engine, event_app
from .server import serve
from .sonarr import Radarr, Sonarr
from .state import State


def installed_version() -> str:
    """Version from pyproject.toml, as installed into the image by pip."""
    try:
        return version("barnaclarr")
    except PackageNotFoundError:
        return "unknown (not installed)"


def main():
    parser = argparse.ArgumentParser(description="Barnaclarr")
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML file; default BARNACLARR_CONFIG or /config/config.toml if present",
    )
    parser.add_argument("--version", action="version", version=installed_version())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    event = sub.add_parser("event")
    event.add_argument("payload", type=Path)
    event.add_argument(
        "--apply", action="store_true", help="Apply only if config dry_run=false too"
    )
    sub.add_parser("jobs")
    retry = sub.add_parser("retry", help="Queue finished jobs again")
    retry.add_argument("ids", type=int, nargs="*", help="Job ids, failed or done")
    retry.add_argument("--failed", action="store_true", help="All failed jobs")
    retry.add_argument(
        "--dry-run", action="store_true", help="All jobs that only planned in dry-run mode"
    )
    args = parser.parse_args()
    if args.command == "retry" and not (args.ids or args.failed or args.dry_run):
        parser.error("retry needs job ids, --failed or --dry-run")
    # DEBUG adds the full JSON report of every job after its summary.
    level = os.environ.get("BARNACLARR_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        parser.error("BARNACLARR_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
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
                engine = Engine(
                    config,
                    Sonarr(config) if config.sonarr_url else None,
                    state,
                    Radarr(config) if config.radarr_url else None,
                )
                payload = json.loads(args.payload.read_text())
                report = engine.process(payload, f"[{event_app(payload)}] event")
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif args.command == "jobs":
                print(
                    json.dumps(
                        [
                            dict(row)
                            for row in state.db.execute(
                                "SELECT id,status,attempts,dry_run,error FROM jobs"
                                " ORDER BY id DESC LIMIT 100"
                            )
                        ],
                        indent=2,
                    )
                )
            elif args.command == "retry":
                ids = state.requeue(args.ids, failed=args.failed, dry_run=args.dry_run)
                print(f"queued {len(ids)} jobs: {' '.join(map(str, ids)) or '-'}")
                if ids and config.dry_run:
                    print("dry run is still on: the jobs will only plan again")
        finally:
            state.close()


if __name__ == "__main__":
    main()
