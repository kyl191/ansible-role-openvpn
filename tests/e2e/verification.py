"""Starts the generated OpenVPN client config and verifies traffic actually
routes through the tunnel (IPv4 and/or IPv6, whichever the instance has)."""

from __future__ import annotations

import ipaddress
import logging
import subprocess
import time
from pathlib import Path

from .models import InstanceInfo, Phase, Status

logger = logging.getLogger(__name__)

TUNNEL_UP_TIMEOUT = 30
TUNNEL_POLL_INTERVAL = 1
CURL_RETRIES = 3
CURL_RETRY_DELAY = 3
VERIFY_CLIENT_NAME = "client1"


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


def verify_instance(instance: InstanceInfo, fetch_base_dir: Path) -> None:
    """Verifies the VPN connection for a single instance (IPv4 and IPv6) in one session."""
    if not instance.is_reachable:
        return

    # Matches tests/ec2.yml's openvpn_fetch_client_configs_per_user_dir: false (flat layout):
    # <openvpn_fetch_client_configs_dir>/<client>-<openvpn_ovpn_server_name>.ovpn
    config_path = fetch_base_dir / f"{VERIFY_CLIENT_NAME}-{instance.hostname}.ovpn"

    if not config_path.exists():
        logger.error(f"Config file not found for {instance.display_name} at {config_path}")
        instance.status = Status.CONFIG_MISSING
        return

    instance.set_phase(Phase.VERIFYING, "starting openvpn client")
    logger.info(f"Testing VPN connectivity for {instance.display_name}...")

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
        instance.phase_detail = "waiting for tunnel"
        if not wait_for_tunnel_up(log_path):
            detail = tail_log(log_path)
            logger.error(f"Tunnel never came up for {instance.display_name}: {detail}")
            instance.status = Status.TUNNEL_FAILED
            instance.failure_detail = detail
            return

        # 3. Test IPv4 (only if instance has a public IPv4 - IPv6-only instances disable the
        # IPv4 tunnel entirely via openvpn_server_network: "", so there's nothing to test)
        if instance.public_ip:
            instance.phase_detail = "testing IPv4 connectivity"
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
            instance.ipv4_status = Status.NOT_APPLICABLE

        # 4. Test IPv6 (only if instance has public IPv6)
        if instance.public_ipv6:
            instance.phase_detail = "testing IPv6 connectivity"
            res6 = curl_with_retry("https://ipv6.icanhazip.com")
            if res6.returncode == 0:
                val = res6.stdout.strip()
                try:
                    ip = ipaddress.IPv6Address(val)
                    instance.vpn_ipv6 = ip
                    if ip == instance.public_ipv6:
                        logger.info(f"IPv6 SUCCESS: {instance.public_ipv6} routed correctly.")
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
            instance.ipv6_status = Status.NOT_APPLICABLE

    except Exception as e:
        logger.error(f"OpenVPN session failed for {instance.display_name}: {e}")
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
                still_alive = subprocess.run(["sudo", "kill", "-0", pid], capture_output=True)
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
    # skipped above (left NOT_APPLICABLE) and don't block an overall PASS.
    ipv4_ok = not instance.public_ip or instance.ipv4_status == Status.PASS
    ipv6_ok = not instance.public_ipv6 or instance.ipv6_status == Status.PASS
    if ipv4_ok and ipv6_ok:
        instance.status = Status.PASS
    else:
        if instance.status != Status.TEST_ERROR:
            instance.status = Status.FAIL
