openvpn
=========

This role installs OpenVPN, configures it as a server, sets up networking (either iptables or firewalld), and can optionally create client certificates.

Tested OSes:
- Fedora 20/21
- CentOS 6/7
- Ubuntu trusty (14.04)

Should be working OSes:
- All Fedora
- Ubuntu trusty & later


Requirements
------------

openvpn must be available as a package in yum/apt! For CentOS users, this role will run `yum install epel-release` to ensure openvpn is available.

Ubuntu precise has a [weird bug](https://bugs.launchpad.net/ubuntu/+source/iptables-persistent/+bug/1002078) that might make the iptables-persistent install fail. There is a [workaround](https://forum.linode.com/viewtopic.php?p=58233#p58233).

Role Variables
--------------

openvpn_port: The port you want OpenVPN to run on.
If you have different ports on different servers, I suggest you set the port in your inventory file.

openvpn_proto: The protocol you want OpenVPN to use (UDP by default)

openvpn\_config\_file: The config file name you want to use (By default `openvpn_{{ openvpn_proto }}_{{ openvpn_port }}`, located in vars/main.yml)

openvpn_redirect_gateway: Whether to push config to the client to redirect its default gateway to the VPN. Default true.

openvpn_set_dns: Whether to set the DNS servers on the client. Default true.

openvpn_enable_management: Boolean indicating whether to open a UNIX domain socket for managing openvpn on /var/run/openvpn/management.

openvpn_server_network: The network address from which you want OpenVPN to choose the addresses for clients. Default 10.9.0.0.

openvpn_server_netmask: The netmask of the client network. Default 255.255.255.0.

openvpn_server_ipv6_network: If set, the network address and prefix of an IPv6 network to assign to clients. By default this is not set, which will cause IPv6 to not be set up on the VPN tunnel. Note that even if this is set, IPv4 will still be used on the VPN tunnel.

openvpn_topology: If set, the "topology" keyword will be set in the server config with the specified value.

These two variables are useful when you have an established system and you want to rebuild the server without reconfiguring all your clients. Note this is security-sensitive data, so you may want to use an Ansible Vault or other mechanism to store these values.

* openvpn_ca_key: This is a dictionary with two elements, "crt" and "key". By default this is not set, so the CA cert and key will be automatically generated if not present on the target system.

* openvpn_tls_auth_key: This is a single item with a pre-generated TLS authentication key.

Dependencies
------------

Does not depend on any other roles

Example Playbook
----------------

    - hosts: vpn
      roles:
        - {role: kyl191.openvpn, clients: [client1, client2],
                            openvpn_port: 4300}

License
-------

GPLv2

Author Information
------------------

Written by Kyle Lexmond
