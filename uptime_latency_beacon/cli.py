"""Monitoring loop and terminal dashboard."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import httpx
from rich.live import Live

from .monitor import load_targets, log_transitions, make_table, run_cycle


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Monitor HTTP availability and latency.")
    result.add_argument("--config", type=Path, default=Path("targets.json"))
    result.add_argument("--interval", type=float, default=60, help="Seconds between checks")
    result.add_argument("--once", action="store_true", help="Run one check and exit")
    result.add_argument("--log-file", type=Path, default=Path("outages.log"))
    result.add_argument("--state-file", type=Path, default=Path("monitor_state.json"))
    return result


def main() -> int:
    args = parser().parse_args()
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero.")
    try:
        targets = load_targets(args.config.expanduser().resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read target configuration: {exc}") from exc
    logging.basicConfig(filename=args.log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    state_path = args.state_file.expanduser().resolve()
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except json.JSONDecodeError:
        previous = {}

    with httpx.Client(headers={"User-Agent": "UptimeLatencyBeacon/1.0"}) as client:
        try:
            if args.once:
                probes = run_cycle(targets, client)
                previous = log_transitions(probes, previous)
                state_path.write_text(json.dumps(previous, indent=2), encoding="utf-8")
                from rich.console import Console
                Console().print(make_table(probes))
                return 0 if all(item.status == "UP" for item in probes) else 1
            with Live(refresh_per_second=4) as live:
                while True:
                    probes = run_cycle(targets, client)
                    previous = log_transitions(probes, previous)
                    state_path.write_text(json.dumps(previous, indent=2), encoding="utf-8")
                    live.update(make_table(probes))
                    time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
