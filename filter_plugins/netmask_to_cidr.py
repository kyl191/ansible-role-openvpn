from __future__ import annotations

import ipaddress


def netmask_to_cidr(netmask):
    return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen


class FilterModule:
    def filters(self):
        return {"netmask_to_cidr": netmask_to_cidr}
