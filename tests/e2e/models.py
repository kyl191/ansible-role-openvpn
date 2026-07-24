"""Data model for a single EC2 instance under test, tracked from discovery
through provisioning and VPN verification."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field
from enum import Enum, auto


class Status(Enum):
    """Final outcome of testing one instance."""

    PENDING = auto()
    PASS = auto()
    FAIL = auto()
    UNREACHABLE = auto()
    CONFIG_MISSING = auto()
    TUNNEL_FAILED = auto()
    TEST_ERROR = auto()

    def __str__(self) -> str:
        return self.name


class Phase(Enum):
    """Where an instance currently sits in the test lifecycle. Drives the status
    board (see e2e.display.StatusBoard) so it's visible what each instance is
    actually waiting on, instead of just silence until the next log line."""

    QUEUED = "queued"
    WAITING_SSH = "waiting for SSH"
    PROVISIONING = "provisioning"
    WAITING_OTHERS = "waiting on others"
    VERIFYING = "verifying VPN"
    DONE = "done"

    def __str__(self) -> str:
        return self.value


@dataclass
class InstanceInfo:
    id: str
    scenario: str = ""
    public_ip: ipaddress.IPv4Address | None = None
    public_ipv6: ipaddress.IPv6Address | None = None
    public_dns: str = ""
    dual_stack_dns: str = ""
    name: str = "Unknown"
    os_name: str = "Unknown"
    # From the EC2 AddressFamily/Architecture tags (terraform-aws-ipv6-v2's ec2.tf) - "unknown"
    # if absent, e.g. a --skip-terraform run against instances from an older terraform state
    # that predates these tags.
    address_family: str = "unknown"
    architecture: str = "unknown"
    ssh_user: str = "ec2-user"
    vpn_ipv4: ipaddress.IPv4Address | None = None
    vpn_ipv6: ipaddress.IPv6Address | None = None
    status: Status = Status.PENDING
    ipv4_status: Status = Status.PENDING
    ipv6_status: Status = Status.PENDING
    failure_detail: str = ""
    playbook_seconds: float | None = None

    phase: Phase = Phase.QUEUED
    phase_detail: str = ""
    phase_started: float = field(default_factory=time.monotonic)

    @property
    def is_reachable(self) -> bool:
        return self.status != Status.UNREACHABLE

    @property
    def hostname(self) -> str:
        """Prefer the AWS dual-stack DNS name (resolves both A and AAAA) so SSH,
        the Ansible inventory, and the generated OpenVPN client config all exercise
        the instance's IPv6 path too, not just IPv4. Falls through to a public IPv6
        literal for IPv6-only instances, which have no public IPv4/DNS name at all."""
        if self.dual_stack_dns:
            return self.dual_stack_dns
        if self.public_dns:
            return self.public_dns
        if self.public_ip:
            return str(self.public_ip)
        if self.public_ipv6:
            return str(self.public_ipv6)
        return ""

    @property
    def display_name(self) -> str:
        """Human-facing label for logs and the status board. Prefers the OS
        detected over SSH (e.g. "Fedora Linux 43") once known; falls back to the
        EC2 Name tag (e.g. "fedora-44-x86-ipv4only", set from terraform's
        instance_config key) which is already OS/scenario-descriptive. Neither the
        DNS name nor the instance ID means anything to a human scanning output."""
        if self.os_name != "Unknown":
            return self.os_name
        if self.name != "Unknown":
            return self.name
        return self.id

    @property
    def category(self) -> str:
        """Short label combining address family and architecture, e.g. "dual-x86_64" -
        a single terraform apply now produces a heterogeneous mix of these (see
        terraform-aws-ipv6-v2), so there's no longer a "which scenario's apply loop
        produced this" signal to group/label instances by. Used both as
        InstanceInfo.scenario (set by orchestrator.run_scenario, for the report's
        Scenario column and log-directory naming) and appended to display_name for
        the status board, so heterogeneous instances stay distinguishable at a glance."""
        return f"{self.address_family}-{self.architecture}"

    def set_phase(self, phase: Phase, detail: str = "") -> None:
        """Advances the lifecycle phase and resets the elapsed-time clock used by
        the status board. `detail` is free text - e.g. the current Ansible task
        name - shown alongside the phase so it's clear what's actually happening."""
        self.phase = phase
        self.phase_detail = detail
        self.phase_started = time.monotonic()

    @property
    def phase_elapsed(self) -> float:
        return time.monotonic() - self.phase_started
