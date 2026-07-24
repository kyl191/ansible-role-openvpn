"""Terraform lifecycle for the shared EC2 test infrastructure.

Every var-file is applied in place, in the same workspace, one after another - not destroyed
and recreated per var-file. Terraform's own diff already tears down any now-absent
`instance_config` entries and creates the new ones; the shared base layer (VPC, security
groups, key pair - see ~/Sync/code/terraform-aws-ipv6-v2/vpc.tf and ec2.tf) never needs to be
rebuilt. In practice there's usually just one var-file - terraform-aws-ipv6-v2's
`instance_config` supports per-entry `address_family`/`spot`, so what used to be four separate
scenario var-files (each needing its own apply/destroy cycle) is now one merged file - but this
still handles more than one if that's ever needed again. A single terraform_destroy after every
var-file has run tears the whole thing down (see e2e.orchestrator.main)."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def terraform_apply(tf_dir: Path, var_file: str, log_path: Path) -> bool:
    """Applies one scenario's var-file in tf_dir's current workspace. Returns False (rather than
    raising) on failure so the caller can still continue on to the next scenario. Full plan/apply
    output goes to `log_path` rather than the console - a full instance-matrix apply's output is
    long enough to bury everything else scrolling past it."""
    logger.info(f"Terraform apply ({var_file})...")
    start = time.monotonic()
    with log_path.open("w") as log_file:
        result = subprocess.run(
            ["terraform", f"-chdir={tf_dir}", "apply", f"-var-file={var_file}", "-auto-approve"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    duration = time.monotonic() - start
    if result.returncode != 0:
        logger.error(f"Terraform apply failed for {var_file} in {duration:.1f}s (log: {log_path}).")
        return False
    logger.info(f"Terraform apply complete for {var_file} in {duration:.1f}s (log: {log_path}).")
    return True


def terraform_destroy(tf_dir: Path, var_file: str, log_path: Path) -> None:
    """Called once, after every scenario has run, so nothing sits around burning the account's
    instance limit. Best-effort: logs and returns rather than raising, since a run that's already
    finished testing shouldn't crash the report generation over a destroy failure."""
    logger.info(f"Terraform destroy ({var_file})...")
    start = time.monotonic()
    with log_path.open("w") as log_file:
        result = subprocess.run(
            ["terraform", f"-chdir={tf_dir}", "destroy", f"-var-file={var_file}", "-auto-approve"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    duration = time.monotonic() - start
    if result.returncode != 0:
        logger.error(
            f"Terraform destroy failed for {var_file} in {duration:.1f}s (log: {log_path}) - "
            f"check {tf_dir} state manually."
        )
    else:
        logger.info(f"Terraform destroy complete for {var_file} in {duration:.1f}s (log: {log_path}).")
