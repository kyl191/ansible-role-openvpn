openvpn
=========

This role installs OpenVPN, configures it as a server, sets up networking (either iptables or firewalld), and can optionally create client certificates.

Requirements
------------

openvpn must be available as a package in yum! For CentOS users, this means running `yum install epel-release` *prior* to running this playbook.

Role Variables
--------------

openvpn_port: The port you want OpenVPN to run on.
openvpn_proto: The protocol you want OpenVPN to use (UDP by default)
openvpn\_config\_file: The config file name you want to use (By default "openvpn\_{{ openvpn\_proto }}\_{{ openvpn\_port }}", located in vars/main.yml)

Dependencies
------------

Does not depend on any other roles

Example Playbook
----------------

Including an example of how to use your role (for instance, with variables passed in as parameters) is always nice for users too:

    - hosts: vpn
      roles:
        - {kyl191.openvpn, clients: [client1, client2],
                            openvpn_port: 4300}

License
-------

GPLv2

Author Information
------------------

Written by Kyle Lexmond
