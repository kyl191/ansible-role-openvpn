"""Markdown summary of a full run, across every scenario."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .models import InstanceInfo

logger = logging.getLogger(__name__)


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


def generate_md_report(results: list[InstanceInfo], report_path: Path) -> None:
    """Writes the report to `report_path` - the caller's run directory, alongside that
    run's logs, so a later run's report never overwrites an earlier one."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render(results))
    logger.info(f"Report generated at {report_path.absolute()}")
