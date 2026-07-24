"""Waiting for an instance to become SSH-reachable, and detecting its OS/user
along the way."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

from .models import InstanceInfo, Phase, Status

logger = logging.getLogger(__name__)

SSH_READY_TIMEOUT = 420
SSH_READY_POLL_INTERVAL = 10
# 300s was too tight in practice: SSH'd into two live instances mid-timeout (2026-07-24) and
# found cloud-init genuinely still working, not hung - modules-final/config-package_update_
# upgrade_install (which runs cloud-init-install-firewalld.yaml on RHEL-family images that don't
# ship firewalld) took 404s on a Rocky 10.1 instance and ~420s on a CentOS Stream 10 instance,
# both completing successfully. Confirmed not a repeat of the IMDS-over-IPv6 boot delay (see
# ADR - init-local finished in under 2s on both); this is real dnf install time, plausibly worse
# now that terraform-aws-ipv6-v2's merged matrix boots ~26 instances at once instead of the old
# per-scenario batches, all competing for egress bandwidth simultaneously.
CLOUD_INIT_TIMEOUT = 600


def determine_ssh_user(instance_name: str, default_user: str) -> str:
    name_lower = instance_name.lower()
    match name_lower:
        case name if name.startswith("fedora"):
            return "fedora"
        case name if name.startswith("ubuntu"):
            return "ubuntu"
        case name if name.startswith("debian"):
            return "admin"
        case name if name.startswith("rocky"):
            return "rocky"
        case _:
            return default_user


def needs_platform_python(instance_name: str) -> bool:
    """RHEL-family v8 hosts (RHEL/CentOS/Alma/Rocky/Oracle) only ship python at
    /usr/libexec/platform-python. ansible-core 2.20 dropped that path from its
    default INTERPRETER_PYTHON_FALLBACK, so auto-discovery no longer finds it."""
    return bool(re.match(r"(almalinux|centos|rhel|rocky|oraclelinux)-8", instance_name.lower()))


def check_ssh_and_detect_os(instance: InstanceInfo, key_path: Path | None, default_user: str) -> bool:
    """Attempts SSH once and, on success, records the actual user/OS. Does not mark the
    instance UNREACHABLE on failure - a single failed attempt during boot is normal and
    expected, not a verdict. wait_for_ssh_ready() below is what decides that."""

    user = determine_ssh_user(instance.name, default_user)
    host = instance.hostname

    cmd: list[str] = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    if key_path:
        cmd.extend(["-i", str(key_path)])

    cmd.extend([f"{user}@{host}", "grep 'PRETTY_NAME=' /etc/os-release"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        instance.ssh_user = user
        if match := re.search(r'PRETTY_NAME="(.+)"', result.stdout):
            instance.os_name = match.group(1)
        else:
            instance.os_name = "Linux (Unknown)"
        return True
    except subprocess.CalledProcessError:
        return False


def wait_for_ssh_ready(
    instance: InstanceInfo,
    key_path: Path | None,
    default_user: str,
    timeout: int = SSH_READY_TIMEOUT,
) -> bool:
    """Retries the actual SSH connection until it succeeds or timeout elapses, rather than a
    single shot-in-the-dark attempt or a proxy signal like EC2 status checks (those only verify
    ARP-level reachability to the kernel and can report healthy well before sshd exists - e.g.
    an IPv6-only instance's cloud-init-local stage can burn several minutes retrying IMDS before
    the network, and therefore sshd, ever comes up)."""
    user = determine_ssh_user(instance.name, default_user)
    instance.set_phase(Phase.WAITING_SSH, f"attempt 1 as {user}")
    logger.info(f"Waiting for SSH on {instance.display_name} ({instance.hostname}) as {user}...")
    deadline = time.monotonic() + timeout
    attempt = 1
    while time.monotonic() < deadline:
        if check_ssh_and_detect_os(instance, key_path, default_user):
            logger.info(f"SSH ready for {instance.display_name} ({instance.hostname}).")
            return True
        attempt += 1
        instance.phase_detail = f"attempt {attempt} as {user}"
        time.sleep(SSH_READY_POLL_INTERVAL)
    logger.error(
        f"SSH never became reachable for {instance.display_name} ({instance.hostname}) "
        f"within {timeout}s."
    )
    instance.status = Status.UNREACHABLE
    return False


def wait_for_cloud_init(
    instance: InstanceInfo, key_path: Path | None, timeout: int = CLOUD_INIT_TIMEOUT
) -> bool:
    """SSH accepting connections only means sshd is up - it says nothing about whether
    cloud-init's own package_update/package installs (see terraform-aws-ipv6-v2's
    cloud-init-install-firewalld.yaml, which installs firewalld via package_update + packages)
    have actually finished. Confirmed for real: a run failed with "No firewall detected, install
    one before proceeding" because our own package_facts snapshot was taken before cloud-init had
    installed firewalld - and separately, cloud-init's package_update holding the dnf/apt lock
    while our own package-manager tasks try to run concurrently is a plausible explanation for
    stalls seen elsewhere with no other cause. `cloud-init status --wait` blocks on the *remote*
    side until cloud-init has genuinely finished (or returns immediately if it already has)."""
    instance.set_phase(Phase.WAITING_SSH, "waiting for cloud-init to finish")
    cmd: list[str] = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
    if key_path:
        cmd.extend(["-i", str(key_path)])
    cmd.extend([f"{instance.ssh_user}@{instance.hostname}", "cloud-init status --wait"])

    try:
        # Exit code isn't checked: cloud-init can finish "degraded" (e.g. a non-fatal warning)
        # and still be genuinely done - what matters here is that it finished at all, not that
        # every stage succeeded. A local subprocess timeout, not `cloud-init status --wait`'s own
        # exit status, is what distinguishes "still running" from "done".
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        logger.error(
            f"cloud-init on {instance.display_name} ({instance.hostname}) didn't finish "
            f"within {timeout}s."
        )
        instance.status = Status.UNREACHABLE
        return False
