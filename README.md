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

Openvpn must be available as a package in yum/apt! For CentOS users, this role will run `yum install epel-release` to ensure openvpn is available.

Ubuntu precise has a [weird bug](https://bugs.launchpad.net/ubuntu/+source/iptables-persistent/+bug/1002078) that might make the iptables-persistent install fail. There is a [workaround](https://forum.linode.com/viewtopic.php?p=58233#p58233).

Role Variables
--------------

| Variable                           | Type    | Choices      | Default                                        | Comment                                                                                                                                     |
|------------------------------------|---------|--------------|------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_key_dir                    | string  |              | /etc/openvpn/keys                              | Path where your server private keys and CA will be stored                                                                                   |
| openvpn_port                       | int     |              | 1194                                           | The port you want OpenVPN to run on. If you have different ports on   different servers, I suggest you set the port in your inventory file. |
| openvpn_proto                      | string  | udp | tcp    | udp                                            | The protocol you want OpenVPN to use (UDP by default)                                                                                       |
| openvpn_config_file                | string  |              | openvpn_{{ openvpn_proto }}_{{ openvpn_port }} |  The config file name you want to   use                                                                                                     |
| openvpn_rsa_bits                   | int     |              | 2048                                           | Number of bit used to protect generated certificates                                                                                        |
| openvpn_service_name               | string  |              | openvpn                                        | Name of the service. Used by systemctl to start the service                                                                                 |
| openvpn_uninstall                  | boolean | true | false | false                                          | Set to true to uninstall the OpenVPN service                                                                                                |
| openvpn_use_pregenerated_dh_params | boolean | true | false | false                                          | DH params are generted with the install by default                                                                                          |
| openvpn_use_modern_tls             | boolean | true | false | true                                           | Use modern Cipher for TLS encryption                                                                                                        |
| openvpn_verify_cn                  | boolean | true | false | false                                          | Check that the CN of the certificate match the FQDN                                                                                         |
| openvpn_redirect_gateway           | boolean | true | false | true                                           | OpenVPN gateway push                                                                                                                        |
| openvpn_set_dns                    | boolean | true | false | true                                           | Will push DNS to the client (Google and OpenDNS)                                                                                            |
| openvpn_enable_management          | boolean | true | false | true                                           |                                                                                                                                             |
| openvpn_server_network             | string  |              | 10.9.0.0                                       | Private network used by OpenVPN service                                                                                                     |
| openvpn_server_netmask             | string  |              | 255.255.255.0                                  | Netmask of the private network                                                                                                              |
| tls_auth_required                  | boolean | true | false | true                                           | Ask the client to push the generated ta.key of the server during the   connection                                                           |
| firewalld_default_interface_zone   | string  |              | public                                         | Firewalld zone where the "ansible_default_ipv4.interface" will   be pushed into                                                             |
| openvpn_server_ipv6_network        | boolean | true | false | false                                          | If set, the network address and prefix of an IPv6 network to assign to   clients. If True, IPv4 still used too.                             |
| openvpn_ca_key                     | dict    |              |                                                | Contain "crt" and "key". If not set, CA cert and key   will be automatically generated on the target system.                                |
| openvpn_tls_auth_key               | string  |              |                                                | Single item with a pre-generated TLS authentication key.                                                                                    |
| openvpn_topology                   | boolean | true | false | false                                          | the "topology" keyword will be set in the server config with   the specified value.                                                         |
| openvpn_use_ldap                   | boolean | true | false | false                                          | Active LDAP backend for authentication. Client certificate not needed   anymore                                                             |
| ldap                               | dict    |              |                                                | Dictionary that contain LDAP configuration                                                                                                  |
|                                    |         |              |                                                |                                                                                                                                             |

LDAP object

| Variable            | Type   | Choices      | Default                                                                                        | Comment                                                                                        |
|---------------------|--------|--------------|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| url                 | string |              | ldap://host.example.com                                                                        | Address of you LDAP backend with syntax ldap[s]://host[:port]                                  |
| anonymous_bind      | string | False | True | False                                                                                          | This is not an Ansible boolean but a string that will be pushed into the   configuration file, |
| bind_dn             | string |              | uid=Manager,ou=People,dc=example,dc=com                                                        | Bind DN used if "anonymous_bind" set to "False"                                                |
| bind_password       | string |              | mysecretpassword                                                                               | Password of the bind_dn user                                                                   |
| tls_enable          | string | yes | no     | no                                                                                             | Force TLS encryption. Not necessary with ldaps addresses                                       |
| tls_ca_cert_file    | string |              | /etc/openvpn/auth/ca.pem                                                                       | Path to the CA ldap backend. This must must has been pushed before                             |
| base_dn             | string |              | ou=People,dc=example,dc=com                                                                    | Base DN where the backend will look for valid user                                             |
| search_filter       | string |              | (&(uid=%u)(accountStatus=active))                                                              | Filter the ldap search                                                                         |
| require_group       | string | False | True | This is not an Ansible boolean but a string that will be pushed into the   configuration file, |                                                                                                |
| group_base_dn       | string |              | ou=Groups,dc=example,dc=com                                                                    | Precise the group to look for. Required if require_group is set to   "True"                    |
| group_search_filter | string |              | (|(cn=developers)(cn=artists))                                                                 | Precise valid groups                                                                           |

Dependencies
------------

Does not depend on any other roles

Example Playbook
----------------

    - hosts: vpn
      gather_facts: true
      roles:
        - {role: kyl191.openvpn, clients: [client1, client2],
                            openvpn_port: 4300}

> **Note:** As the role will need to know the remote used platform (32 or 64 bits), you must set `gather_facts` to `true` in your play.

License
-------

GPLv2

Author Information
------------------

Written by Kyle Lexmond
