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
    scenario: str = ""
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
    playbook_seconds: float | None = None

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

    raw_instances = list(ec2.instances.filter(Filters=filters))

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
                public_ip=ipaddress.IPv4Address(instance.public_ip_address)
                if instance.public_ip_address
                else None,
                public_ipv6=public_ipv6,
                public_dns=instance.public_dns_name,
                dual_stack_dns=dual_stack_dns,
                name=name,
            )
        )

    # Keep only instances with some reachable address - either a public IPv4 (dual-stack) or a
    # public IPv6 (dual-stack or IPv6-only).
    instances = [inst for inst in instances if inst.public_ip or inst.public_ipv6]

    logger.info(f"Found {len(instances)} running instances with a reachable address.")
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


SSH_READY_TIMEOUT = 420
SSH_READY_POLL_INTERVAL = 10


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
    logger.info(f"Waiting for SSH on {instance.id} ({instance.hostname}) as {user}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_ssh_and_detect_os(instance, key_path, default_user):
            logger.info(f"SSH ready for {instance.id} ({instance.hostname}).")
            return True
        time.sleep(SSH_READY_POLL_INTERVAL)
    logger.error(
        f"SSH never became reachable for {instance.id} ({instance.hostname}) within {timeout}s."
    )
    instance.status = Status.UNREACHABLE
    return False


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


def provision_instance(inst: InstanceInfo, key_path: Path | None) -> bool:
    """Runs the playbook against a single instance in its own ansible-playbook subprocess, with
    its own single-host inventory, so its measured wall-clock time is independent of how slow or
    fast any other instance in the same scenario is. A single combined inventory + one playbook
    run would only ever measure the slowest host, since Ansible's default linear strategy
    locksteps every host at each task boundary - useless for comparing instance types/sizes
    against each other, which is the point of timing this at all."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as inventory:
        inventory.write(_inventory_line(inst, key_path))
        inventory_path = Path(inventory.name)

    cmd: list[str] = ["ansible-playbook", "-i", str(inventory_path), "tests/ec2.yml"]
    start = time.monotonic()
    success = False
    try:
        # Stream output line-by-line with a hostname prefix instead of buffering it all via
        # capture_output and only printing at the end - several of these run concurrently in
        # separate threads, so each line needs its own prefix to stay attributable, but a
        # multi-minute silent wait per instance is worse than that. Merging stderr into stdout
        # keeps ordering sane without needing two reader threads per instance.
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert process.stdout is not None
        for line in process.stdout:
            logger.info(f"[{inst.hostname}] {line.rstrip()}")
        process.wait()
        success = process.returncode == 0
        return success
    finally:
        duration = time.monotonic() - start
        inst.playbook_seconds = duration
        logger.info(
            f"{inst.hostname}: playbook {'complete' if success else 'failed'} in {duration:.1f}s."
        )
        if inventory_path.exists():
            inventory_path.unlink()


def run_ansible_parallel(instances: list[InstanceInfo], key_path: Path | None) -> bool:
    """Provisions every reachable instance concurrently, each via its own ansible-playbook
    subprocess (see provision_instance) so per-instance timing is genuinely independent."""
    reachable_instances = [i for i in instances if i.is_reachable]

    if not reachable_instances:
        logger.error("No reachable instances to provision.")
        return False

    logger.info(f"Provisioning {len(reachable_instances)} instances in parallel...")

    with ThreadPoolExecutor() as executor:
        results = list(
            executor.map(lambda inst: provision_instance(inst, key_path), reachable_instances)
        )

    return all(results)


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


CURL_RETRIES = 3
CURL_RETRY_DELAY = 3


def curl_with_retry(url: str) -> subprocess.CompletedProcess[str]:
    """Retries a failed curl a few times before giving up. DNS resolution through a freshly
    (re)established tunnel can race the resolver's per-link scope registration for a few
    seconds right after "Initialization Sequence Completed" - confirmed by hand: the identical
    curl failed with "Resolving timed out", then succeeded immediately on retry a few seconds
    later with no other change. Returns the last attempt either way, so its stdout/stderr are
    still available for a genuine (non-transient) failure."""
    result = subprocess.run(
        f"curl -sS --connect-timeout 10 {url}", shell=True, capture_output=True, text=True
    )
    for _ in range(CURL_RETRIES - 1):
        if result.returncode == 0:
            return result
        time.sleep(CURL_RETRY_DELAY)
        result = subprocess.run(
            f"curl -sS --connect-timeout 10 {url}", shell=True, capture_output=True, text=True
        )
    return result


VERIFY_CLIENT_NAME = "client1"


def verify_instance(instance: InstanceInfo, fetch_base_dir: Path) -> None:
    """Verifies the VPN connection for a single instance (IPv4 and IPv6) in one session."""
    if not instance.is_reachable:
        return

    # Matches tests/ec2.yml's openvpn_fetch_client_configs_per_user_dir: false (flat layout):
    # <openvpn_fetch_client_configs_dir>/<client>-<openvpn_ovpn_server_name>.ovpn
    config_path = fetch_base_dir / f"{VERIFY_CLIENT_NAME}-{instance.hostname}.ovpn"

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

        # 3. Test IPv4 (only if instance has a public IPv4 - IPv6-only instances disable the
        # IPv4 tunnel entirely via openvpn_server_network: "", so there's nothing to test)
        if instance.public_ip:
            res4 = curl_with_retry("https://ipv4.icanhazip.com")
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
        else:
            instance.ipv4_status = Status.PENDING

        # 4. Test IPv6 (only if instance has public IPv6)
        if instance.public_ipv6:
            res6 = curl_with_retry("https://ipv6.icanhazip.com")
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
        # 5. Stop OpenVPN gracefully (SIGTERM, not -9). OpenVPN's DCO backend owns a real
        # kernel tun interface + routes; SIGKILL skips its cleanup and leaves that interface
        # orphaned (NO-CARRIER but still routed), and the next run's tunnel gets duplicate
        # equal-metric routes where the kernel can silently pick the dead one over the live
        # one. Only fall back to -9 if it won't die on its own.
        if pid_file.exists():
            pid = pid_file.read_text().strip()
            subprocess.run(["sudo", "kill", pid], capture_output=True)
            for _ in range(10):
                still_alive = subprocess.run(
                    ["sudo", "kill", "-0", pid], capture_output=True
                )
                if still_alive.returncode != 0:
                    break
                time.sleep(0.5)
            else:
                logger.warning(f"OpenVPN pid {pid} didn't exit after SIGTERM; forcing.")
                subprocess.run(["sudo", "kill", "-9", pid], capture_output=True)
            subprocess.run(["sudo", "rm", "-f", str(pid_file)], capture_output=True)
        subprocess.run(["sudo", "rm", "-f", str(log_path)], capture_output=True)

    # Overall Status update - PASS requires each address family that's actually present
    # (public_ip / public_ipv6) to have tested PASS; families the instance doesn't have are
    # skipped above (left PENDING) and don't block an overall PASS.
    ipv4_ok = not instance.public_ip or instance.ipv4_status == Status.PASS
    ipv6_ok = not instance.public_ipv6 or instance.ipv6_status == Status.PASS
    if ipv4_ok and ipv6_ok:
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
            "| Scenario | Instance ID | Name | OS | Public IPv4 | Public IPv6 | VPN IPv4 | VPN IPv6 | IPv4 Status | IPv6 Status | Overall | Playbook Time (s) | Detail |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for res in results:
            detail = res.failure_detail.replace("|", "\\|")
            playbook_time = (
                f"{res.playbook_seconds:.1f}" if res.playbook_seconds is not None else "N/A"
            )
            f.write(
                f"| {res.scenario} | {res.id} | {res.name} | {res.os_name} | {res.public_ip or 'N/A'} | {res.public_ipv6 or 'N/A'} | "
                f"{res.vpn_ipv4 or 'N/A'} | {res.vpn_ipv6 or 'N/A'} | {res.ipv4_status} | {res.ipv6_status} | {res.status} | {playbook_time} | {detail} |\n"
            )
    logger.info(f"Report generated at {report_path.absolute()}")


def terraform_apply(tf_dir: Path, var_file: str) -> bool:
    """Applies one scenario's var-file in tf_dir's current workspace. Returns False (rather than
    raising) on failure so the caller can still attempt a cleanup destroy afterwards."""
    logger.info(f"Terraform apply ({var_file})...")
    result = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "apply", f"-var-file={var_file}", "-auto-approve"],
    )
    if result.returncode != 0:
        logger.error(f"Terraform apply failed for {var_file}.")
        return False
    return True


def terraform_destroy(tf_dir: Path, var_file: str) -> None:
    """Always called after a scenario, even if apply or testing failed, so a bad scenario
    doesn't sit there burning the account's instance limit for the next one."""
    logger.info(f"Terraform destroy ({var_file})...")
    result = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "destroy", f"-var-file={var_file}", "-auto-approve"],
    )
    if result.returncode != 0:
        logger.error(
            f"Terraform destroy failed for {var_file} - check {tf_dir} state manually."
        )


def run_scenario(
    scenario: str,
    region: str,
    profile: str | None,
    tag_key: str | None,
    tag_value: str | None,
    ssh_key_path: Path | None,
    default_user: str,
) -> list[InstanceInfo]:
    """Discovers, provisions, and verifies whatever's currently running for one scenario."""
    instances = get_instances(region, profile, tag_key, tag_value)
    if not instances:
        logger.error(f"No instances found for scenario {scenario}.")
        return []
    for inst in instances:
        inst.scenario = scenario

    with ThreadPoolExecutor() as executor:
        list(
            executor.map(
                lambda inst: wait_for_ssh_ready(inst, ssh_key_path, default_user),
                instances,
            )
        )

    run_ansible_parallel(instances, ssh_key_path)

    fetch_dir = Path("/tmp/ansible-openvpn-certs")
    for inst in instances:
        verify_instance(inst, fetch_dir)

    return instances


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
    parser.add_argument(
        "--skip-terraform",
        action="store_true",
        help="Skip terraform apply/destroy entirely and test whatever's currently running "
        "(e.g. a scenario you applied by hand for debugging)",
    )

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

    tf_config = config.get("terraform", {})
    tf_dir_str = tf_config.get("dir")
    var_files = tf_config.get("var_files", [])

    all_instances: list[InstanceInfo] = []

    if args.skip_terraform:
        logger.info("--skip-terraform set: testing whatever's currently running, no terraform.")
        all_instances = run_scenario(
            "manual", region, profile, tag_key, tag_value, ssh_key_path, default_user
        )
    elif tf_dir_str and var_files:
        tf_dir = Path(tf_dir_str).expanduser()
        for var_file in var_files:
            logger.info(f"=== Scenario: {var_file} ===")
            applied = terraform_apply(tf_dir, var_file)
            try:
                if applied:
                    scenario_instances = run_scenario(
                        var_file, region, profile, tag_key, tag_value, ssh_key_path, default_user
                    )
                    all_instances.extend(scenario_instances)
                else:
                    logger.error(f"Skipping tests for {var_file} since terraform apply failed.")
            except Exception:
                logger.exception(
                    f"Scenario {var_file} raised an unexpected error; continuing to the next scenario."
                )
            finally:
                terraform_destroy(tf_dir, var_file)
    else:
        # No terraform lifecycle configured - test whatever's already running.
        all_instances = run_scenario(
            "manual", region, profile, tag_key, tag_value, ssh_key_path, default_user
        )

    if not all_instances:
        logger.error("No instances found across any scenario.")
        sys.exit(0)

    generate_md_report(all_instances)


if __name__ == "__main__":
    main()
