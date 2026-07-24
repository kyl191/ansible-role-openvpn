"""Runs the role's ansible-playbook against each reachable instance."""

from __future__ import annotations

import logging
import re
import select
import subprocess
import tempfile
import time
from pathlib import Path

from .models import InstanceInfo, Phase
from .ssh import needs_platform_python

logger = logging.getLogger(__name__)

# Killed if no output at all arrives for this long - a stuck task (a hung SSH reconnect, DNS
# resolution, whatever) would otherwise block this instance's thread, and therefore the whole
# scenario, indefinitely. This bounds how long a single stuck task can run, not the whole
# playbook - a legitimately slow-but-still-progressing run isn't affected. Comfortably above
# every normal task observed so far (package installs run ~20-40s), comfortably below a real
# hang (one Fedora run sat on "Gathering Facts" - normally near-instant - for 180s).
PROVISION_STALL_TIMEOUT = 90

# tests/ec2.yml references the role by this path, passed in via -e, rather than a relative
# "../kyl191.openvpn": that only resolves because ansible's role search re-appends the checkout
# directory's own name, which breaks the moment the checkout isn't literally named
# "kyl191.openvpn" (e.g. a git worktree) - confirmed empirically that even a bare ".." silently
# resolves to a role with zero tasks instead of erroring. A fully-resolved absolute path, computed
# here rather than guessed at in YAML, has no such dependency.
ROLE_PATH = Path(__file__).resolve().parents[2]

# Matches ansible's default callback output, e.g. `TASK [kyl191.openvpn : Install packages] ***`
_TASK_LINE = re.compile(r"^TASK \[(?P<name>.+?)\]")

# Matches a task's own result line, e.g. `ok: [host]`, `changed: [host]`, `skipping: [host]`,
# `fatal: [host]: FAILED! => {...}`. Distinguishes "stalled while this task was still running"
# from "this task already finished, stalled waiting for the next task's banner" - a real gap
# found in practice: the task named in a stall's error message had actually already completed
# (a "skipping:" line for it was already in the log) when the process was killed.
_RESULT_LINE = re.compile(r"^(ok|changed|skipping|failed|fatal):\s*\[")

# display_name is meant for humans, not filesystems - a detected PRETTY_NAME like
# "Debian GNU/Linux 13 (trixie)" contains a literal "/" and would otherwise be read as
# a subdirectory.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.-]+")


def _safe_filename_component(text: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", text).strip("_") or "unknown"


def instance_log_path(log_dir: Path, inst: InstanceInfo) -> Path:
    return log_dir / f"{_safe_filename_component(inst.display_name)}-{inst.id}.log"


def _timing_path(log_path: Path) -> Path:
    return log_path.with_name(f"{log_path.stem}-timings.log")


def _write_task_timings(
    timing_path: Path, inst: InstanceInfo, task_durations: list[tuple[str, float]], total: float
) -> None:
    """Per-task breakdown, separate from the raw ansible-playbook log - written even on a
    failed/interrupted run, so the last entry shows how long the run got stuck on before
    dying rather than just an overall total."""
    lines = [f"# {inst.display_name} ({inst.id}) - {total:.1f}s total across {len(task_durations)} tasks\n"]
    lines += [f"{duration:7.1f}s  {name}\n" for name, duration in task_durations]
    timing_path.write_text("".join(lines))


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
    same stream, so it's still visible what each host is doing without the flood. The same task
    boundaries are timed and written to a separate `<log_path>-timings.log` file (see
    _write_task_timings), so a slow task doesn't require digging through the raw log for it.
    Killed if stuck - see PROVISION_STALL_TIMEOUT."""
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
    stalled = False
    task_durations: list[tuple[str, float]] = []
    current_task: str | None = None
    current_task_done = False
    task_started = start
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
            while True:
                ready, _, _ = select.select([process.stdout], [], [], PROVISION_STALL_TIMEOUT)
                if not ready:
                    stalled = True
                    break
                line = process.stdout.readline()
                if not line:
                    break  # EOF: the process exited
                log_file.write(line)
                if match := _TASK_LINE.match(line):
                    # The role has no meta name, so ansible prefixes every task with its
                    # resolved path (ROLE_PATH) instead - e.g. "/abs/path/to/role : config |
                    # Copy service file". Keep just the task name for the status board.
                    _, _, task_name = match.group("name").rpartition(" : ")
                    inst.phase_detail = task_name
                    now = time.monotonic()
                    if current_task is not None:
                        task_durations.append((current_task, now - task_started))
                    current_task, task_started = task_name, now
                    current_task_done = False
                elif _RESULT_LINE.match(line):
                    current_task_done = True

        if stalled:
            process.kill()
        process.wait()
        success = not stalled and process.returncode == 0
        return success
    finally:
        duration = time.monotonic() - start
        inst.playbook_seconds = duration
        if current_task is not None:
            task_durations.append((current_task, time.monotonic() - task_started))
        timing_path = _timing_path(log_path)
        _write_task_timings(timing_path, inst, task_durations, duration)
        # Every instance runs in its own thread (see orchestrator._wait_and_provision) and they
        # don't all finish at once - without this, a fast instance would sit here showing its
        # last task name under "provisioning" until the slowest sibling finishes too, looking
        # stuck. Phase.WAITING_OTHERS already says "waiting on others" - no need to repeat that
        # here too, just the outcome.
        if stalled:
            if current_task is None:
                where = "before the first task"
            elif current_task_done:
                # The last-seen task already produced a result line (ok/changed/skipping/failed)
                # before the silence started - it wasn't what hung, ansible was just slow to move
                # on to whatever comes after it.
                where = f"after '{current_task}' finished, waiting for the next task"
            else:
                where = f"on '{current_task}'"
            outcome = f"FAILED (no output for {PROVISION_STALL_TIMEOUT}s {where})"
            logger.error(f"{inst.display_name}: killed after {duration:.1f}s - {outcome}.")
        else:
            outcome = "complete" if success else "FAILED"
            logger.info(
                f"{inst.display_name}: playbook {'complete' if success else 'failed'} in "
                f"{duration:.1f}s (log: {log_path}, timings: {timing_path})."
            )
        inst.set_phase(Phase.WAITING_OTHERS, f"playbook {outcome}")
        if inventory_path.exists():
            inventory_path.unlink()
