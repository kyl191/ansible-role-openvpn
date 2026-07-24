"""CLI argument parsing and TOML config loading, merged into one RunSettings.

CLI flags always win over the TOML file so secrets (e.g. --ssh-key) never need
to live on disk. This module only parses - it doesn't log or validate against
the filesystem, since logging isn't configured yet when it runs (see
e2e.orchestrator.main for where those checks happen once it is)."""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AWSSettings:
    region: str
    profile: str | None
    tag_key: str | None
    tag_value: str | None


@dataclass
class SSHSettings:
    key_path: Path | None
    default_user: str


@dataclass
class TerraformSettings:
    dir: Path | None
    var_files: list[str]

    @property
    def configured(self) -> bool:
        return self.dir is not None and bool(self.var_files)


@dataclass
class RunSettings:
    aws: AWSSettings
    ssh: SSHSettings
    terraform: TerraformSettings
    skip_terraform: bool


def _load_toml(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as f:
        return tomllib.load(f)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run E2E tests for OpenVPN Ansible Role against EC2"
    )
    parser.add_argument(
        "--config", default="tests/e2e_config.toml", help="Path to TOML config file"
    )
    parser.add_argument("--ssh-key", help="Path to SSH private key (overrides config)")
    parser.add_argument("--region", help="AWS Region (overrides config)")
    parser.add_argument("--profile", help="AWS CLI Profile (overrides config)")
    parser.add_argument(
        "--skip-terraform",
        action="store_true",
        help="Skip terraform apply/destroy entirely and test whatever's currently running "
        "(e.g. a scenario you applied by hand for debugging)",
    )
    return parser.parse_args(argv)


def load_settings(argv: list[str] | None = None) -> RunSettings:
    """Merges the TOML config file with CLI overrides (CLI wins) into one settings object."""
    args = parse_args(argv)
    config = _load_toml(Path(args.config))

    aws_config = config.get("aws", {})
    ssh_config = config.get("ssh", {})
    tf_config = config.get("terraform", {})

    ssh_key_str = args.ssh_key or ssh_config.get("key_path")
    tf_dir_str = tf_config.get("dir")

    return RunSettings(
        aws=AWSSettings(
            region=args.region or aws_config.get("region", "us-east-1"),
            profile=args.profile or aws_config.get("profile"),
            tag_key=aws_config.get("tag_key"),
            tag_value=aws_config.get("tag_value"),
        ),
        ssh=SSHSettings(
            key_path=Path(ssh_key_str) if ssh_key_str else None,
            default_user=ssh_config.get("default_user", "ec2-user"),
        ),
        terraform=TerraformSettings(
            dir=Path(tf_dir_str).expanduser() if tf_dir_str else None,
            var_files=tf_config.get("var_files", []),
        ),
        skip_terraform=args.skip_terraform,
    )
