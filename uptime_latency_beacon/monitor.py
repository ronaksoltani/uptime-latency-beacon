"""HTTP checks, dashboard rendering, and outage transition logging."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

LOG = logging.getLogger("uptime-beacon")


@dataclass(frozen=True)
class Probe:
    name: str
    url: str
    status: str
    status_code: int | None
    latency_ms: float | None
    detail: str
    checked_at: datetime


def load_targets(config_path: Path) -> list[dict[str, Any]]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    targets = data.get("targets", [])
    if not targets:
        raise ValueError("Configuration must include at least one target.")
    return targets


def check_target(target: dict[str, Any], client: httpx.Client) -> Probe:
    checked_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    try:
        response = client.get(
            target["url"],
            timeout=float(target.get("timeout_seconds", 8)),
            follow_redirects=True,
        )
        latency = (time.perf_counter() - start) * 1000
        expected = target.get("expected_statuses", [200])
        status = "UP" if response.status_code in expected else "DOWN"
        detail = "Expected response" if status == "UP" else "Unexpected HTTP status"
        return Probe(target["name"], target["url"], status, response.status_code,
                     latency, detail, checked_at)
    except (httpx.RequestError, ValueError, KeyError) as exc:
        latency = (time.perf_counter() - start) * 1000
        return Probe(target.get("name", target.get("url", "Unknown")),
                     target.get("url", ""), "DOWN", None, latency,
                     str(exc)[:160], checked_at)


def make_table(probes: list[Probe]) -> Table:
    table = Table(title="Uptime & Latency Beacon", expand=True)
    table.add_column("Target", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("HTTP", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Checked (UTC)")
    table.add_column("Details")
    for probe in probes:
        status_style = "green" if probe.status == "UP" else "red"
        latency = f"{probe.latency_ms:.0f} ms" if probe.latency_ms is not None else "—"
        table.add_row(
            probe.name,
            f"[{status_style}]{probe.status}[/{status_style}]",
            str(probe.status_code) if probe.status_code is not None else "—",
            latency,
            probe.checked_at.strftime("%H:%M:%S"),
            probe.detail,
        )
    return table


def log_transitions(probes: list[Probe], previous: dict[str, str]) -> dict[str, str]:
    current = {}
    for probe in probes:
        current[probe.name] = probe.status
        old = previous.get(probe.name)
        if probe.status == "DOWN" and old != "DOWN":
            LOG.error("OUTAGE name=%s url=%s detail=%s", probe.name, probe.url, probe.detail)
        elif probe.status == "UP" and old == "DOWN":
            LOG.info("RECOVERY name=%s url=%s", probe.name, probe.url)
    return current


def mark_slow(probes: list[Probe], targets: list[dict[str, Any]]) -> list[Probe]:
    thresholds = {item["name"]: float(item.get("slow_threshold_ms", 1000)) for item in targets}
    result = []
    for probe in probes:
        limit = thresholds.get(probe.name, 1000)
        if probe.status == "UP" and probe.latency_ms is not None and probe.latency_ms > limit:
            result.append(Probe(probe.name, probe.url, probe.status, probe.status_code,
                                probe.latency_ms, f"SLOW · {probe.detail} (>{limit:g} ms)", probe.checked_at))
            LOG.warning("SLOW name=%s latency_ms=%.1f threshold_ms=%.1f",
                        probe.name, probe.latency_ms, limit)
        else:
            result.append(probe)
    return result


def run_cycle(targets: list[dict[str, Any]], client: httpx.Client) -> list[Probe]:
    return mark_slow([check_target(target, client) for target in targets], targets)


def print_table(probes: list[Probe]) -> None:
    Console().print(make_table(probes))
