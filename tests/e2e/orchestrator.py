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

from .aws import get_instances
from .config import RunSettings, load_settings
from .display import StatusBoard, setup_logging
from .models import InstanceInfo, Phase
from .provisioning import run_ansible_parallel
from .report import generate_md_report
from .ssh import wait_for_ssh_ready
from .terraform import terraform_apply, terraform_destroy
from .verification import verify_instance

logger = logging.getLogger(__name__)

FETCH_DIR = Path("/tmp/ansible-openvpn-certs")
# Durable run output (logs, report) lives under here, one timestamped subdirectory per run - not
# in the repo tree, and never cleaned up automatically: unlike the ephemeral per-verification
# OpenVPN client log (see verification.py), this is the whole point of the run and needs to
# outlive it.
LOG_ROOT = Path(tempfile.gettempdir()) / "ansible-openvpn-e2e"


def run_scenario(
    scenario: str,
    settings: RunSettings,
    console: Console,
    log_dir: Path,
) -> list[InstanceInfo]:
    """Discovers, provisions, and verifies whatever's currently running for one scenario.
    Runs a fresh StatusBoard for the duration - SSH-wait, provisioning, and verification are
    exactly the phases that used to be silent for minutes at a time, so that's what it covers.
    Terraform apply/destroy happen outside this, printed to the console as normal."""
    instances = get_instances(
        settings.aws.region, settings.aws.profile, settings.aws.tag_key, settings.aws.tag_value
    )
    if not instances:
        logger.error(f"No instances found for scenario {scenario}.")
        return []
    for inst in instances:
        inst.scenario = scenario

    with StatusBoard(console) as board:
        board.start_scenario(scenario, instances)

        with ThreadPoolExecutor() as executor:
            list(
                executor.map(
                    lambda inst: wait_for_ssh_ready(
                        inst, settings.ssh.key_path, settings.ssh.default_user
                    ),
                    instances,
                )
            )

        run_ansible_parallel(instances, settings.ssh.key_path, log_dir / scenario)

        for inst in instances:
            verify_instance(inst, FETCH_DIR)
            inst.set_phase(Phase.DONE, str(inst.status))

    return instances


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
            if not terraform_apply(tf_dir, var_file):
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
        destroy_var_file = last_applied_var_file or settings.terraform.var_files[0]
        terraform_destroy(tf_dir, destroy_var_file)

    return all_instances


def main(argv: list[str] | None = None) -> None:
    settings = load_settings(argv)

    run_dir = LOG_ROOT / time.strftime("%Y%m%d-%H%M%S")
    console = setup_logging(run_dir)

    if settings.ssh.key_path and not settings.ssh.key_path.exists():
        logger.warning(f"SSH Key file {settings.ssh.key_path} does not exist.")

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
