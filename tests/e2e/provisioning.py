"""Runs the role's ansible-playbook against each reachable instance."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .models import InstanceInfo, Phase
from .ssh import needs_platform_python

logger = logging.getLogger(__name__)

# tests/ec2.yml references the role by this path, passed in via -e, rather than a relative
# "../kyl191.openvpn": that only resolves because ansible's role search re-appends the checkout
# directory's own name, which breaks the moment the checkout isn't literally named
# "kyl191.openvpn" (e.g. a git worktree) - confirmed empirically that even a bare ".." silently
# resolves to a role with zero tasks instead of erroring. A fully-resolved absolute path, computed
# here rather than guessed at in YAML, has no such dependency.
ROLE_PATH = Path(__file__).resolve().parents[2]

# Matches ansible's default callback output, e.g. `TASK [kyl191.openvpn : Install packages] ***`
_TASK_LINE = re.compile(r"^TASK \[(?P<name>.+?)\]")

# display_name is meant for humans, not filesystems - a detected PRETTY_NAME like
# "Debian GNU/Linux 13 (trixie)" contains a literal "/" and would otherwise be read as
# a subdirectory.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.-]+")


def _safe_filename_component(text: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", text).strip("_") or "unknown"


def _inventory_line(inst: InstanceInfo, key_path: Path | None) -> str:
    line = f"{inst.hostname} ansible_user={inst.ssh_user} "
    if key_path:
        line += f"ansible_ssh_private_key_file={key_path} "
    if needs_platform_python(inst.name):
        line += "ansible_python_interpreter=/usr/libexec/platform-python "
    else:
        line += "ansible_python_interpreter=auto_silent "
    line += "ansible_ssh_common_args='-o StrictHostKeyChecking=no' "
    line += f"openvpn_server_hostname={inst.hostname} "
    if not inst.public_ip:
        # Genuinely IPv4-less host (see terraform-ipv6-only.tfvars) - exercise the
        # role's IPv4-tunnel-disabled path instead of assuming dual-stack.
        line += 'openvpn_server_network="" '
    if not inst.public_ipv6:
        # Genuinely IPv6-less host (see terraform-ipv4-only.tfvars) - exercise the
        # role's IPv6-tunnel-disabled path instead of assuming dual-stack.
        line += 'openvpn_server_ipv6_network="" '
    return line.rstrip() + "\n"


def provision_instance(inst: InstanceInfo, key_path: Path | None, log_path: Path) -> bool:
    """Runs the playbook against a single instance in its own ansible-playbook subprocess, with
    its own single-host inventory, so its measured wall-clock time is independent of how slow or
    fast any other instance in the same scenario is. A single combined inventory + one playbook
    run would only ever measure the slowest host, since Ansible's default linear strategy
    locksteps every host at each task boundary - useless for comparing instance types/sizes
    against each other, which is the point of timing this at all.

    Full output is written to `log_path` rather than the console logger - several of these run
    concurrently in separate threads, and hundreds of interleaved task lines per host would drown
    out everything else. The status board instead shows the current task name, parsed from the
    same stream, so it's still visible what each host is doing without the flood."""
    inst.set_phase(Phase.PROVISIONING, "starting ansible-playbook")

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as inventory:
        inventory.write(_inventory_line(inst, key_path))
        inventory_path = Path(inventory.name)

    cmd: list[str] = [
        "ansible-playbook",
        "-i",
        str(inventory_path),
        "-e",
        f"openvpn_role_path={ROLE_PATH}",
        "tests/ec2.yml",
    ]
    start = time.monotonic()
    success = False
    try:
        # stdin=DEVNULL: several of these run concurrently (see orchestrator._wait_and_provision),
        # and without this they'd all inherit the same shared stdin file descriptor from this
        # process. Ansible sets that fd to blocking mode at startup; when multiple concurrent
        # ansible-playbook processes race on the same underlying fd, one instance's toggle can
        # flip it out from under another, which ansible then refuses to run against at all
        # ("Ansible requires blocking IO... Non-blocking file handles detected: <stdin>").
        # A non-interactive test runner has no business feeding these processes stdin anyway.
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        with log_path.open("w") as log_file:
            for line in process.stdout:
                log_file.write(line)
                if match := _TASK_LINE.match(line):
                    # The role has no meta name, so ansible prefixes every task with its
                    # resolved path (ROLE_PATH) instead - e.g. "/abs/path/to/role : config |
                    # Copy service file". Keep just the task name for the status board.
                    _, _, task_name = match.group("name").rpartition(" : ")
                    inst.phase_detail = task_name
        process.wait()
        success = process.returncode == 0
        return success
    finally:
        duration = time.monotonic() - start
        inst.playbook_seconds = duration
        # Every instance runs in its own thread (see run_ansible_parallel) and they don't all
        # finish at once - without this, a fast instance would sit here showing its last task
        # name under "provisioning" until the slowest sibling finishes too, looking stuck.
        outcome = "complete" if success else "FAILED"
        inst.set_phase(Phase.WAITING_OTHERS, f"playbook {outcome}, waiting for other instances")
        logger.info(
            f"{inst.display_name}: playbook {'complete' if success else 'failed'} in "
            f"{duration:.1f}s (log: {log_path})."
        )
        if inventory_path.exists():
            inventory_path.unlink()


def run_ansible_parallel(instances: list[InstanceInfo], key_path: Path | None, log_dir: Path) -> bool:
    """Provisions every reachable instance concurrently, each via its own ansible-playbook
    subprocess (see provision_instance) so per-instance timing is genuinely independent."""
    reachable_instances = [i for i in instances if i.is_reachable]

    if not reachable_instances:
        logger.error("No reachable instances to provision.")
        return False

    logger.info(f"Provisioning {len(reachable_instances)} instances in parallel...")
    log_dir.mkdir(parents=True, exist_ok=True)

    def _run(inst: InstanceInfo) -> bool:
        log_path = log_dir / f"{_safe_filename_component(inst.display_name)}-{inst.id}.log"
        return provision_instance(inst, key_path, log_path)

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(_run, reachable_instances))

    return all(results)
