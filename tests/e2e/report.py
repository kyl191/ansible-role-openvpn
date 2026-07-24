"""Markdown summary of a full run, across every scenario."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .models import InstanceInfo

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = Path("tests/e2e_report.md")


def _render(results: list[InstanceInfo]) -> str:
    lines = [
        "# End-to-End Test Report",
        "",
        f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Scenario | Instance ID | Name | OS | Public IPv4 | Public IPv6 | VPN IPv4 | VPN IPv6 | "
        "IPv4 Status | IPv6 Status | Overall | Playbook Time (s) | Detail |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for res in results:
        detail = res.failure_detail.replace("|", "\\|")
        playbook_time = f"{res.playbook_seconds:.1f}" if res.playbook_seconds is not None else "N/A"
        lines.append(
            f"| {res.scenario} | {res.id} | {res.name} | {res.os_name} | {res.public_ip or 'N/A'} | "
            f"{res.public_ipv6 or 'N/A'} | {res.vpn_ipv4 or 'N/A'} | {res.vpn_ipv6 or 'N/A'} | "
            f"{res.ipv4_status} | {res.ipv6_status} | {res.status} | {playbook_time} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def generate_md_report(
    results: list[InstanceInfo],
    filename: str | Path = DEFAULT_REPORT_PATH,
    archive_dir: Path | None = None,
) -> None:
    """Writes the report to `filename` (default: tests/e2e_report.md, matched by
    tooling/habit) and, if `archive_dir` is given, also drops a durable copy there
    alongside that run's logs so the report doesn't get overwritten by the next run."""
    content = _render(results)

    report_path = Path(filename)
    report_path.write_text(content)
    logger.info(f"Report generated at {report_path.absolute()}")

    if archive_dir is not None:
        archive_path = archive_dir / "e2e_report.md"
        archive_path.write_text(content)
