openvpn
=========

This role installs OpenVPN, configures it as a server, sets up networking (either iptables or firewalld), and can optionally create client certificates.

Tested OSes:
- Fedora 20/21
- CentOS 6
- Ubuntu trusty (14.04)

Should be working OSes:
- All Fedora
- CentOS 6/7
- Ubuntu trusty & later


Requirements
------------

openvpn must be available as a package in yum/apt! For CentOS users, this means running `yum install epel-release` *prior* to running this playbook.

Ubuntu precise has a [weird bug](https://bugs.launchpad.net/ubuntu/+source/iptables-persistent/+bug/1002078) that might make the iptables-persistent install fail. There is a [workaround](https://forum.linode.com/viewtopic.php?p=58233#p58233).

Role Variables
--------------

openvpn_port: The port you want OpenVPN to run on.
If you have different ports on different servers, I suggest you set the port in your inventory file.

openvpn_proto: The protocol you want OpenVPN to use (UDP by default)

openvpn\_config\_file: The config file name you want to use (By default `openvpn_{{ openvpn_proto }}_{{ openvpn_port }}`, located in vars/main.yml)

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
