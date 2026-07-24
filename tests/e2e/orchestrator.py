"""Top-level orchestration: wires config, terraform lifecycle, per-scenario
instance testing, and reporting together."""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console
from rich.text import Text

from .aws import get_instances
from .config import RunSettings, load_settings
from .display import StatusBoard, setup_logging
from .models import InstanceInfo, Phase
from .provisioning import instance_log_path, provision_instance
from .report import generate_md_report
from .ssh import wait_for_cloud_init, wait_for_ssh_ready
from .terraform import terraform_apply, terraform_destroy
from .verification import verify_instance

logger = logging.getLogger(__name__)

FETCH_DIR = Path("/tmp/ansible-openvpn-certs")
# Durable run output (logs, report) lives under here, one timestamped subdirectory per run - not
# in the repo tree, and never cleaned up automatically: unlike the ephemeral per-verification
# OpenVPN client log (see verification.py), this is the whole point of the run and needs to
# outlive it.
LOG_ROOT = Path(tempfile.gettempdir()) / "ansible-openvpn-e2e"
# Explicit rather than ThreadPoolExecutor's default (min(32, cpu_count+4) - 20 on a 16-core
# machine): these are I/O-bound (SSH/network), not CPU-bound, so sizing off cpu_count
# undersells the actual safe concurrency. terraform-aws-ipv6-v2's merged matrix runs up to 26
# instances in one scenario (was capped at 13 per scenario pre-merge) - comfortable headroom
# above that so a bigger matrix later doesn't silently start queueing instances again.
INSTANCE_THREAD_POOL_SIZE = 40


def _wait_and_provision(inst: InstanceInfo, settings: RunSettings, log_dir: Path) -> None:
    """Waits for this instance's own SSH readiness, then its own cloud-init completion, then
    immediately provisions it - instances don't wait on each other. Each instance's
    ansible-playbook run is already an independent subprocess (see provisioning.py), so there's
    no reason a slow-booting sibling (e.g. an IPv6-only instance's cloud-init taking minutes
    longer) should hold up everyone else from starting; the old two-stage "wait for all, then
    provision all" batching did exactly that.

    The cloud-init wait matters on its own, not just for pacing: SSH accepting connections
    doesn't mean cloud-init's own package installs are done (see wait_for_cloud_init) - skipping
    this step let a run fail with "No firewall detected" because our package_facts snapshot was
    taken before cloud-init had actually installed firewalld."""
    if not wait_for_ssh_ready(inst, settings.ssh.key_path, settings.ssh.default_user):
        return
    if not wait_for_cloud_init(inst, settings.ssh.key_path):
        return
    provision_instance(inst, settings.ssh.key_path, instance_log_path(log_dir, inst))


def run_scenario(
    scenario: str,
    settings: RunSettings,
    console: Console,
    log_dir: Path,
) -> list[InstanceInfo]:
    """Discovers, provisions, and verifies whatever's currently running for one scenario.
    Runs a fresh StatusBoard for the duration - SSH-wait, provisioning, and verification are
    exactly the phases that used to be silent for minutes at a time, so that's what it covers.
    Terraform apply/destroy happen outside this - see e2e.terraform, which logs its own
    summary line and writes full plan/apply output to a file rather than the console."""
    instances = get_instances(
        settings.aws.region, settings.aws.profile, settings.aws.tag_key, settings.aws.tag_value
    )
    if not instances:
        logger.error(f"No instances found for scenario {scenario}.")
        return []
    # inst.scenario is the per-instance category (e.g. "dual-x86_64"), not the `scenario`
    # parameter (which var-file/apply batch this came from) - a single apply now produces a
    # heterogeneous mix of address families/architectures (see terraform-aws-ipv6-v2), so
    # there's no longer a "which apply loop produced this" signal worth showing per instance.
    # `scenario` itself is still used below for the status board title and log-directory name.
    for inst in instances:
        inst.scenario = inst.category

    scenario_log_dir = log_dir / scenario
    scenario_log_dir.mkdir(parents=True, exist_ok=True)

    with StatusBoard(console) as board:
        board.start_scenario(scenario, instances)

        logger.info(f"Waiting for SSH and provisioning {len(instances)} instances independently...")
        with ThreadPoolExecutor(max_workers=INSTANCE_THREAD_POOL_SIZE) as executor:
            list(
                executor.map(
                    lambda inst: _wait_and_provision(inst, settings, scenario_log_dir), instances
                )
            )

        for inst in instances:
            verify_instance(inst, FETCH_DIR)
            inst.set_phase(Phase.DONE, str(inst.status))

    return instances


def _pause_for_inspection(instances: list[InstanceInfo], settings: RunSettings, console: Console) -> None:
    """Blocks on Enter, after printing a ready-to-paste SSH command per instance - a fixed
    sleep can't substitute for this since there's no way to guess how long "SSH in and look
    around" takes. A Ctrl+C here is treated as "stop waiting", not "abandon the instances":
    still falls through to terraform_destroy in the caller's `finally` rather than propagating
    and leaving billable instances orphaned."""
    console.print("\n[bold yellow]--pause-before-destroy set - instances are still up.[/bold yellow]")
    key_flag = f"-i {settings.ssh.key_path} " if settings.ssh.key_path else ""
    for inst in sorted(instances, key=lambda i: i.display_name):
        if not inst.hostname:
            continue
        # display_name is detected text (e.g. an OS PRETTY_NAME) - Text() renders it plain
        # rather than parsing it as markup. See display.py's StatusBoard for the same fix
        # after a real task name containing "[...]" got silently mangled by console.print.
        console.print(Text(f"  {inst.display_name:<40} ssh {key_flag}{inst.ssh_user}@{inst.hostname}"))
    try:
        input("\nPress Enter to continue with terraform destroy... ")
    except KeyboardInterrupt:
        console.print("\nInterrupted - proceeding with teardown.")


def _run_scenarios_with_terraform(
    settings: RunSettings, console: Console, log_dir: Path
) -> list[InstanceInfo]:
    """Applies each scenario's var-file in place, in sequence, in the same terraform
    workspace - never destroying between scenarios, since terraform's own diff already
    tears down the previous scenario's instances and the shared base layer (VPC,
    security groups, key pair) doesn't need rebuilding every time. One destroy, after
    everything has run, is enough (see e2e.terraform for the rationale)."""
    tf_dir = settings.terraform.dir
    assert tf_dir is not None
    all_instances: list[InstanceInfo] = []
    last_applied_var_file: str | None = None

    try:
        for var_file in settings.terraform.var_files:
            logger.info(f"=== Scenario: {var_file} ===")
            apply_log = log_dir / f"terraform-apply-{var_file}.log"
            if not terraform_apply(tf_dir, var_file, apply_log):
                logger.error(f"Skipping tests for {var_file} since terraform apply failed.")
                continue
            last_applied_var_file = var_file
            try:
                all_instances.extend(run_scenario(var_file, settings, console, log_dir))
            except Exception:
                logger.exception(
                    f"Scenario {var_file} raised an unexpected error; continuing to the "
                    "next scenario."
                )
    finally:
        if settings.pause_before_destroy and all_instances:
            _pause_for_inspection(all_instances, settings, console)
        destroy_var_file = last_applied_var_file or settings.terraform.var_files[0]
        destroy_log = log_dir / f"terraform-destroy-{destroy_var_file}.log"
        terraform_destroy(tf_dir, destroy_var_file, destroy_log)

    return all_instances


def main(argv: list[str] | None = None) -> None:
    settings = load_settings(argv)

    run_dir = LOG_ROOT / time.strftime("%Y%m%d-%H%M%S")
    console = setup_logging(run_dir)

    if settings.ssh.key_path and not settings.ssh.key_path.exists():
        logger.warning(f"SSH Key file {settings.ssh.key_path} does not exist.")

    will_run_terraform_destroy = settings.terraform.configured and not settings.skip_terraform
    if settings.pause_before_destroy and not will_run_terraform_destroy:
        logger.warning("--pause-before-destroy has no effect here: nothing in this run will "
                        "call terraform destroy.")

    if settings.skip_terraform:
        logger.info("--skip-terraform set: testing whatever's currently running, no terraform.")
        all_instances = run_scenario("manual", settings, console, run_dir)
    elif settings.terraform.configured:
        all_instances = _run_scenarios_with_terraform(settings, console, run_dir)
    else:
        # No terraform lifecycle configured - test whatever's already running.
        all_instances = run_scenario("manual", settings, console, run_dir)

    if not all_instances:
        logger.error("No instances found across any scenario.")
        sys.exit(0)

    generate_md_report(all_instances, run_dir / "e2e_report.md")
