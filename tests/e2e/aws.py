"""EC2 instance discovery for the current scenario."""

from __future__ import annotations

import ipaddress
import logging

import boto3

from .models import InstanceInfo

logger = logging.getLogger(__name__)


def get_instances(
    region: str,
    profile: str | None = None,
    tag_key: str | None = None,
    tag_value: str | None = None,
) -> list[InstanceInfo]:
    """Enumerates running EC2 instances."""
    logger.info(f"Enumerating instances in {region} (Profile: {profile or 'default'})...")

    session = boto3.Session(profile_name=profile, region_name=region)
    ec2 = session.resource("ec2")
    filters = [{"Name": "instance-state-name", "Values": ["running"]}]

    if tag_key and tag_value:
        filters.append({"Name": f"tag:{tag_key}", "Values": [tag_value]})

    raw_instances = list(ec2.instances.filter(Filters=filters))

    # Look up each instance's public dual-stack DNS name (resolves to both the
    # public IPv4 and public IPv6 address) in one batched call.
    eni_ids = [ni.id for instance in raw_instances for ni in instance.network_interfaces]
    dual_stack_dns_by_eni: dict[str, str] = (
        {
            ni.id: ni.public_ip_dns_name_options.get("PublicDualStackDnsName", "")
            for ni in ec2.network_interfaces.filter(NetworkInterfaceIds=eni_ids)
        }
        if eni_ids
        else {}
    )

    instances: list[InstanceInfo] = []
    for instance in raw_instances:
        name = "Unknown"
        address_family = "unknown"
        architecture = "unknown"
        if instance.tags:
            for tag in instance.tags:
                if tag["Key"] == "Name":
                    name = tag["Value"]
                elif tag["Key"] == "AddressFamily":
                    address_family = tag["Value"]
                elif tag["Key"] == "Architecture":
                    architecture = tag["Value"]

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
                address_family=address_family,
                architecture=architecture,
            )
        )

    # Keep only instances with some reachable address - either a public IPv4 (dual-stack) or a
    # public IPv6 (dual-stack or IPv6-only).
    instances = [inst for inst in instances if inst.public_ip or inst.public_ipv6]

    # Sort by the EC2 Name tag rather than display_name: it's stable from discovery onward,
    # unlike display_name, which starts as this same tag but later switches to the detected OS
    # once SSH succeeds - sorting by display_name would visually reorder the status board and
    # report mid-run as each instance's SSH check completes at a different time.
    instances.sort(key=lambda inst: inst.name)

    logger.info(f"Found {len(instances)} running instances with a reachable address.")
    return instances
