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
