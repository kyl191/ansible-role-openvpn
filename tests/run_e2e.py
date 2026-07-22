#!/usr/bin/env python3
import argparse
import boto3
import subprocess
import tempfile
import sys
import time
import tomllib
import re
import logging
import ipaddress
from typing import Any
from enum import Enum, auto
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class Status(Enum):
    PENDING = auto()
    PASS = auto()
    FAIL = auto()
    UNREACHABLE = auto()
    CONFIG_MISSING = auto()
    TUNNEL_FAILED = auto()
    TEST_ERROR = auto()

    def __str__(self):
        return self.name


@dataclass
class InstanceInfo:
    id: str
    public_ip: ipaddress.IPv4Address | None = None
    public_ipv6: ipaddress.IPv6Address | None = None
    public_dns: str = ""
    dual_stack_dns: str = ""
    name: str = "Unknown"
    os_name: str = "Unknown"
    ssh_user: str = "ec2-user"
    vpn_ipv4: ipaddress.IPv4Address | None = None
    vpn_ipv6: ipaddress.IPv6Address | None = None
    status: Status = Status.PENDING
    ipv4_status: Status = Status.PENDING
    ipv6_status: Status = Status.PENDING
    failure_detail: str = ""

    @property
    def is_reachable(self) -> bool:
        return self.status != Status.UNREACHABLE

    # TODO: openvpn_server_network: "" (IPv4 tunnel disabled) is only verified today via CI
    # container tests with a route deleted post-install - not a genuinely IPv4-less host. Add an
    # IPv6-only subnet EC2 scenario here (Terraform config in ~/Sync/code/terraform-aws-ipv6/)
    # to prove it end-to-end on a host with no IPv4 connectivity at all.

    @property
    def hostname(self) -> str:
        """Prefer the AWS dual-stack DNS name (resolves both A and AAAA) so SSH,
        the Ansible inventory, and the generated OpenVPN client config all exercise
        the instance's IPv6 path too, not just IPv4."""
        if self.dual_stack_dns:
            return self.dual_stack_dns
        return self.public_dns if self.public_dns else str(self.public_ip)


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as f:
        return tomllib.load(f)


def get_instances(
    region: str,
    profile: str | None = None,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> list[InstanceInfo]:
    """Enumerates running EC2 instances."""
    logger.info(
        f"Enumerating instances in {region} (Profile: {profile or 'default'})..."
    )

    session = boto3.Session(profile_name=profile, region_name=region)
    ec2 = session.resource("ec2")
    filters = [{"Name": "instance-state-name", "Values": ["running"]}]

    if tag_key and tag_value:
        filters.append({"Name": f"tag:{tag_key}", "Values": [tag_value]})

    raw_instances = [
        instance
        for instance in ec2.instances.filter(Filters=filters)
        if instance.public_ip_address
    ]

    # Look up each instance's public dual-stack DNS name (resolves to both the
    # public IPv4 and public IPv6 address) in one batched call.
    eni_ids = [ni.id for instance in raw_instances for ni in instance.network_interfaces]
    dual_stack_dns_by_eni: dict[str, str] = {
        ni.id: ni.public_ip_dns_name_options.get("PublicDualStackDnsName", "")
        for ni in ec2.network_interfaces.filter(NetworkInterfaceIds=eni_ids)
    } if eni_ids else {}

    instances: list[InstanceInfo] = []
    for instance in raw_instances:
        name = "Unknown"
        if instance.tags:
            for tag in instance.tags:
                if tag["Key"] == "Name":
                    name = tag["Value"]

        public_ipv6 = None
        dual_stack_dns = ""
        for ni in instance.network_interfaces:
            if ni.ipv6_addresses:
                public_ipv6 = ipaddress.IPv6Address(ni.ipv6_addresses[0]["Ipv6Address"])
                dual_stack_dns = dual_stack_dns_by_eni.get(ni.id, "")
                break

        instances.append(
            InstanceInfo(
                id=instance.id,
                public_ip=ipaddress.IPv4Address(instance.public_ip_address),
                public_ipv6=public_ipv6,
                public_dns=instance.public_dns_name,
                dual_stack_dns=dual_stack_dns,
                name=name,
            )
        )

    logger.info(f"Found {len(instances)} running instances with public IPs.")
    return instances


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


def check_ssh_and_detect_os(
    instance: InstanceInfo, key_path: Path | None, default_user: str
) -> bool:
    """Checks SSH connectivity and updates instance info with User and OS."""

    user = determine_ssh_user(instance.name, default_user)
    host = instance.hostname

    logger.info(f"Checking SSH for {instance.id} ({host}) as {user}...")

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
        logger.error(f"Failed to SSH into {host} as {user}")
        instance.status = Status.UNREACHABLE
        return False


def run_ansible_parallel(instances: list[InstanceInfo], key_path: Path | None) -> bool:
    """Generates a combined inventory and runs the playbook once."""
    reachable_instances = [i for i in instances if i.is_reachable]

    if not reachable_instances:
        logger.error("No reachable instances to provision.")
        return False

    logger.info(f"Provisioning {len(reachable_instances)} instances in parallel...")

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as inventory:
        for inst in reachable_instances:
            line = f"{inst.hostname} ansible_user={inst.ssh_user} "
            if key_path:
                line += f"ansible_ssh_private_key_file={key_path} "
            if needs_platform_python(inst.name):
                line += "ansible_python_interpreter=/usr/libexec/platform-python "
            else:
                line += "ansible_python_interpreter=auto_silent "
            line += "ansible_ssh_common_args='-o StrictHostKeyChecking=no' "
            line += f"openvpn_server_hostname={inst.hostname}\n"
            inventory.write(line)

        inventory_path = Path(inventory.name)

    try:
        cmd: list[str] = [
            "ansible-playbook",
            "-i",
            str(inventory_path),
            "tests/ec2.yml",
        ]
        subprocess.run(cmd, check=True)
        logger.info("Ansible provisioning complete.")
        return True
    except subprocess.CalledProcessError:
        logger.error("Ansible provisioning failed (some hosts may have failed).")
        return False
    finally:
        if inventory_path.exists():
            inventory_path.unlink()


TUNNEL_UP_TIMEOUT = 30
TUNNEL_POLL_INTERVAL = 1


def wait_for_tunnel_up(log_path: Path, timeout: int = TUNNEL_UP_TIMEOUT) -> bool:
    """Polls the OpenVPN client log for the tunnel-established marker, instead of
    guessing with a fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = subprocess.run(
            ["sudo", "grep", "-q", "Initialization Sequence Completed", str(log_path)],
            capture_output=True,
        )
        if res.returncode == 0:
            return True
        time.sleep(TUNNEL_POLL_INTERVAL)
    return False


def tail_log(log_path: Path, lines: int = 15) -> str:
    res = subprocess.run(
        ["sudo", "tail", "-n", str(lines), str(log_path)],
        capture_output=True,
        text=True,
    )
    return " ".join(res.stdout.split())


def verify_instance(instance: InstanceInfo, fetch_base_dir: Path) -> None:
    """Verifies the VPN connection for a single instance (IPv4 and IPv6) in one session."""
    if not instance.is_reachable:
        return

    # Structure: fetch_base_dir / client1 / hostname / client.ovpn (user's fetch_dir points to client1)
    config_path = fetch_base_dir / f"{instance.hostname}.ovpn"

    if not config_path.exists():
        logger.error(f"Config file not found for {instance.hostname} at {config_path}")
        instance.status = Status.CONFIG_MISSING
        return

    logger.info(f"Testing VPN connectivity for {instance.hostname}...")

    pid_file = Path(f"/tmp/openvpn_{instance.id}.pid")
    log_path = Path(f"/tmp/openvpn_{instance.id}.log")

    try:
        # 1. Start OpenVPN
        subprocess.run(
            f"sudo /usr/bin/openvpn --config {config_path} --daemon "
            f"--writepid {pid_file} --log {log_path}",
            shell=True,
            check=True,
            capture_output=True,
        )

        # 2. Wait for the tunnel to actually come up instead of guessing with a fixed sleep
        if not wait_for_tunnel_up(log_path):
            detail = tail_log(log_path)
            logger.error(f"Tunnel never came up for {instance.hostname}: {detail}")
            instance.status = Status.TUNNEL_FAILED
            instance.failure_detail = detail
            return

        # 3. Test IPv4
        res4 = subprocess.run(
            "curl -s --connect-timeout 10 https://ipv4.icanhazip.com",
            shell=True,
            capture_output=True,
            text=True,
        )
        if res4.returncode == 0:
            val = res4.stdout.strip()
            try:
                ip = ipaddress.IPv4Address(val)
                instance.vpn_ipv4 = ip
                if ip == instance.public_ip:
                    logger.info(f"IPv4 SUCCESS: {instance.public_ip} routed correctly.")
                    instance.ipv4_status = Status.PASS
                else:
                    msg = f"IPv4 routed via {ip}, expected {instance.public_ip}"
                    logger.error(f"IPv4 FAILURE: {msg}")
                    instance.ipv4_status = Status.FAIL
                    instance.failure_detail += f"{msg}. "
            except ValueError:
                logger.error(f"Invalid IPv4 received: {val}")
                instance.ipv4_status = Status.TEST_ERROR
                instance.failure_detail += f"Invalid IPv4 received: {val}. "
        else:
            logger.error(f"IPv4 curl failed: {res4.stderr}")
            instance.ipv4_status = Status.TEST_ERROR
            instance.failure_detail += f"IPv4 curl failed: {res4.stderr.strip()}. "

        # 4. Test IPv6 (only if instance has public IPv6)
        if instance.public_ipv6:
            res6 = subprocess.run(
                "curl -s --connect-timeout 10 https://ipv6.icanhazip.com",
                shell=True,
                capture_output=True,
                text=True,
            )
            if res6.returncode == 0:
                val = res6.stdout.strip()
                try:
                    ip = ipaddress.IPv6Address(val)
                    instance.vpn_ipv6 = ip
                    if ip == instance.public_ipv6:
                        logger.info(
                            f"IPv6 SUCCESS: {instance.public_ipv6} routed correctly."
                        )
                        instance.ipv6_status = Status.PASS
                    else:
                        msg = f"IPv6 routed via {ip}, expected {instance.public_ipv6}"
                        logger.error(f"IPv6 FAILURE: {msg}")
                        instance.ipv6_status = Status.FAIL
                        instance.failure_detail += f"{msg}. "
                except ValueError:
                    logger.error(f"Invalid IPv6 received: {val}")
                    instance.ipv6_status = Status.TEST_ERROR
                    instance.failure_detail += f"Invalid IPv6 received: {val}. "
            else:
                logger.error(f"IPv6 curl failed: {res6.stderr}")
                instance.ipv6_status = Status.TEST_ERROR
                instance.failure_detail += f"IPv6 curl failed: {res6.stderr.strip()}. "
        else:
            instance.ipv6_status = Status.PENDING

    except Exception as e:
        logger.error(f"OpenVPN session failed for {instance.hostname}: {e}")
        instance.status = Status.TEST_ERROR
        instance.failure_detail += f"Exception: {e}. "
    finally:
        # 5. Stop OpenVPN
        if pid_file.exists():
            subprocess.run(
                f"sudo kill -9 $(cat {pid_file}) && sudo rm {pid_file}",
                shell=True,
                capture_output=True,
            )
        subprocess.run(["sudo", "rm", "-f", str(log_path)], capture_output=True)

    # Overall Status update
    if instance.ipv4_status == Status.PASS and (
        not instance.public_ipv6 or instance.ipv6_status == Status.PASS
    ):
        instance.status = Status.PASS
    else:
        if instance.status != Status.TEST_ERROR:
            instance.status = Status.FAIL


def generate_md_report(
    results: list[InstanceInfo], filename: str | Path = "tests/e2e_report.md"
) -> None:
    report_path = Path(filename)
    with report_path.open("w") as f:
        f.write("# End-to-End Test Report\n\n")
        f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(
            "| Instance ID | Name | OS | Public IPv4 | Public IPv6 | VPN IPv4 | VPN IPv6 | IPv4 Status | IPv6 Status | Overall | Detail |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for res in results:
            detail = res.failure_detail.replace("|", "\\|")
            f.write(
                f"| {res.id} | {res.name} | {res.os_name} | {res.public_ip} | {res.public_ipv6 or 'N/A'} | "
                f"{res.vpn_ipv4 or 'N/A'} | {res.vpn_ipv6 or 'N/A'} | {res.ipv4_status} | {res.ipv6_status} | {res.status} | {detail} |\n"
            )
    logger.info(f"Report generated at {report_path.absolute()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run E2E tests for OpenVPN Ansible Role against EC2"
    )
    parser.add_argument(
        "--config", default="tests/e2e_config.toml", help="Path to TOML config file"
    )
    parser.add_argument("--ssh-key", help="Path to SSH private key (overrides config)")
    parser.add_argument("--region", help="AWS Region (overrides config)")
    parser.add_argument("--profile", help="AWS CLI Profile (overrides config)")

    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)

    region = args.region or config.get("aws", {}).get("region", "us-east-1")
    profile = args.profile or config.get("aws", {}).get("profile")
    ssh_key_str = args.ssh_key or config.get("ssh", {}).get("key_path")
    default_user = config.get("ssh", {}).get("default_user", "ec2-user")
    tag_key = config.get("aws", {}).get("tag_key")
    tag_value = config.get("aws", {}).get("tag_value")

    ssh_key_path: Path | None = None
    if ssh_key_str:
        ssh_key_path = Path(ssh_key_str)
        if not ssh_key_path.exists():
            logger.warning(f"SSH Key file {ssh_key_path} does not exist.")

    instances = get_instances(region, profile, tag_key, tag_value)
    if not instances:
        logger.error("No instances found.")
        sys.exit(0)

    # Parallel OS Detection
    with ThreadPoolExecutor() as executor:
        list(
            executor.map(
                lambda inst: check_ssh_and_detect_os(inst, ssh_key_path, default_user),
                instances,
            )
        )

    # Provisioning (Parallel via Ansible itself)
    run_ansible_parallel(instances, ssh_key_path)

    # Parallel Verification
    fetch_dir = Path("/tmp/ansible/client1")
    for inst in instances:
        verify_instance(inst, fetch_dir)

    generate_md_report(instances)


if __name__ == "__main__":
    main()
