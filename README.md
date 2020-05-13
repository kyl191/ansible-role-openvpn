openvpn
=========
[![Build Status](https://travis-ci.org/kyl191/ansible-role-openvpn.svg?branch=master)](https://travis-ci.org/kyl191/ansible-role-openvpn)

This role installs OpenVPN, configures it as a server, sets up networking (either iptables or firewalld), and can optionally create client certificates.

Tested OSes [(TravisCI)](https://travis-ci.org/kyl191/ansible-role-openvpn):
- Fedora 28+
- CentOS 7


Requirements
------------

Openvpn must be available as a package in yum/apt! For CentOS users, this role will run `yum install epel-release` to ensure openvpn is available.

Ubuntu precise has a [weird bug](https://bugs.launchpad.net/ubuntu/+source/iptables-persistent/+bug/1002078) that might make the iptables-persistent install fail. There is a [workaround](https://forum.linode.com/viewtopic.php?p=58233#p58233).

Role Variables
--------------

| Variable                           | Type    | Choices      | Default                                        | Comment                                                                                                                                                           |
|------------------------------------|---------|--------------|------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_base_dir                   | string  |              | /etc/openvpn                                   | Path where your OpenVPN config will be stored                                                                                                                     |
| openvpn_key_dir                    | string  |              | /etc/openvpn/keys                              | Path where your server private keys and CA will be stored                                                                                                         |
| openvpn_local                      | string  |              |                                                | Local host name or IP address for bind.  If specified, OpenVPN will bind to this address only.  If unspecified, OpenVPN will bind to all interfaces.              |
| openvpn_port                       | int     |              | 1194                                           | The port you want OpenVPN to run on. If you have different ports on   different servers, I suggest you set the port in your inventory file.                       |
| openvpn_server_hostname            | string  |              | `{{inventory_hostname}}`                       | The server name to place in the client configuration file (if different from the `inventory_hostname`             |
| openvpn_proto | string  | udp, tcp | udp | The protocol you want OpenVPN to use |
| openvpn_dualstack | boolean | | true | Whether or not to use a dualstack (IPv4 + v6) socket |
| openvpn_config_file                | string  |              | openvpn_{{ openvpn\_proto }}_{{ openvpn_port }} |  The config file name you want to   use                                                                                                                          |
| openvpn_rsa_bits                   | int     |              | 2048                                           | Number of bit used to protect generated certificates                                                                                                              |
| openvpn_service_name               | string  |              | openvpn                                        | Name of the service. Used by systemctl to start the service                                                                                                       |
| openvpn_uninstall                  | boolean | true , false | false                                          | Set to true to uninstall the OpenVPN service                                                                                                                      |
| openvpn_use_pregenerated_dh_params | boolean | true , false | false                                          | DH params are generted with the install by default                                                                                                                |
| openvpn_use_hardened_tls           | boolean | true , false | true                                           | Require a minimum version of TLS 1.2                                                                                                                              |
| openvpn_use_modern_tls             | boolean | true , false | true                                           | Use modern Cipher for TLS encryption (Not recommended with OpenVPN 2.4)                                                                                           |
| openvpn_verify_cn                  | boolean | true , false | false                                          | Check that the CN of the certificate match the FQDN                                                                                                               |
| openvpn_redirect_gateway           | boolean | true , false | true                                           | OpenVPN gateway push                                                                                                                                              |
| openvpn_set_dns                    | boolean | true , false | true                                           | Will push DNS to the client (Cloudflare and Google)                                                                                                               |
| openvpn_enable_management          | boolean | true , false | true                                           |                                                                                                                                                                   |
| openvpn_management_bind            | string  |              | /var/run/openvpn/management unix               | The interface to bind on for the management interface. Can be unix or TCP socket.                                                                                 |
| openvpn_management_client_user     | string  |              | root                                           | Use this user when using a Unix socket for management interface.                                                                                                  |
| openvpn_server_network             | string  |              | 10.9.0.0                                       | Private network used by OpenVPN service                                                                                                                           |
| openvpn_server_netmask             | string  |              | 255.255.255.0                                  | Netmask of the private network                                                                                                                                    |
| tls_auth_required                  | boolean | true , false | true                                           | Ask the client to push the generated ta.key of the server during the   connection                                                                                 |
| firewalld_default_interface_zone   | string  |              | public                                         | Firewalld zone where the "ansible_default_ipv4.interface" will   be pushed into                                                                                   |
| openvpn_server_ipv6_network        | boolean | true , false | false                                          | If set, the network address and prefix of an IPv6 network to assign to   clients. If True, IPv4 still used too.                                                   |
| openvpn_ca_key                     | dict    |              |                                                | Contain "crt" and "key". If not set, CA cert and key   will be automatically generated on the target system.                                                      |
| openvpn_tls_auth_key               | string  |              |                                                | Single item with a pre-generated TLS authentication key.                                                                                                          |
| openvpn_topology                   | boolean | true , false | false                                          | the "topology" keyword will be set in the server config with   the specified value.                                                                               |
| openvpn_push                       | list    |              | empty                                          | Set here a list of string that will be placed as "push   "<string>". E.g `- route 10.20.30.0 255.255.255.0` will generate push "route 10.20.30.0 255.255.255.0"   |
| openvpn_use_ldap                   | boolean | true , false | false                                          | Active LDAP backend for authentication. Client certificate not needed   anymore                                                                                   |
| ldap                               | dict    |              |                                                | Dictionary that contain LDAP configuration                                                                                                                        |
| manage_firewall_rules              | boolean | true , false | true                                           | Allow playbook to manage iptables                                                                                                                                 |
| openvpn_crl_path                   | string  |              |                                                | Define a path to the CRL file for revokations.                                                                                                                    |
| openvpn_use_crl                    | boolean | true , false |                                                | Configure OpenVPN server to honor certificate revocation list.                                                                                                    |
| openvpn_revoke_these_certs         | list    |              |                                                | List of client certificates to revoke.
| openvpn_client_register_dns        | boolean | true , false | true                                           | Add `register-dns` option to client config (Windows only).                                                                                                        |
| openvpn_duplicate_cn               | boolean | true , false | false                                          | Add `duplicate-cn` option to server config - this allows clients to connect multiple times with the one key. NOTE: client ip addresses won't be static anymore!   |
| openvpn_addl_server_options        | list    |              | empty                                          | List of user-defined server options that are not already present in the server template. (e.g. `- ping-timer-rem`)
| openvpn_addl_client_options        | list    |              | empty                                          | List of user-defined client options that are not already present in the client template. (e.g. `- mssfix 1400`)
| openvpn_status_version             | int     | 1, 2, 3      | 1                                              | Define the formatting of the openvpn-status.log file where are listed current client connection                                                                   |
| openvpn_resolv_retry               | int/string | any int, infinite | 5                                      | Hostname resolv failure retry seconds. Set "infinite" to retry indefinitely in case of poor connection or laptop sleep mode recovery etc.                         |
| openvpn_client_to_client           | boolean | true, false  | false                                          | Set to true if you want clients to access each other.                                                                                                             |
| openvpn_masquerade_not_snat        | boolean | true, false  | false                                          | Set to true if you want to set up MASQUERADE instead of the default SNAT in iptables.                                                 |
| openvpn_compression                | string  |              | lzo                                            | Set `compress` compression option. Empty for no compression.                                                                                                      |
| openvpn_cipher                     | string  |              | AES-256-CBC                                    | Set `cipher` option for server and client.   
| openvpn_auth_alg                   | string  |              | SHA256                                         | Set `auth` authentication algoritm.                                                                                                                               |
| openvpn_tun_mtu                    | int     |              |                                                | Set `tun-mtu` value. Empty for default.                                                                                                                           |
| openvpn_log_dir                    | string  |              | /var/log                                       | Set location of openvpn log files. This parameter is a part of `log-append` configuration value.                                                                  |
| openvpn_log_file                   | string  |              | openvpn.log                                    | Set log filename. This parameter is a part of `log-append` configuration value.                                                                                   |
| openvpn_logrotate_config           | string  |              | See defaults/main.yml                          | Configure logrotate script.                                                                                                                                       |
| openvpn_keepalive_ping             | int     |              | 5                                              | Set `keepalive` ping interval seconds.                                                                                                                            |
| openvpn_keepalive_timeout          | int     |              | 30                                             | Set `keepalive` timeout seconds                                                                                                                                   |
| openvpn_service_user               | string  |              | nobody                                         | Set the openvpn service user.                                                                                                                                     |
| openvpn_service_group              | string  |              | nogroup                                        | Set the openvpn service group.                                                                                                                                    |


LDAP object

| Variable            | Type   | Choices      | Default                                 | Comment                                                                                        |
|---------------------|--------|--------------|-----------------------------------------|------------------------------------------------------------------------------------------------|
| url                 | string |              | ldap://host.example.com                 | Address of you LDAP backend with syntax ldap[s]://host[:port]                                  |
| anonymous_bind      | string | False , True | False                                   | This is not an Ansible boolean but a string that will be pushed into the   configuration file, |
| bind_dn             | string |              | uid=Manager,ou=People,dc=example,dc=com | Bind DN used if "anonymous_bind" set to "False"                                                |
| bind_password       | string |              | mysecretpassword                        | Password of the bind_dn user                                                                   |
| tls_enable          | string | yes , no     | no                                      | Force TLS encryption. Not necessary with ldaps addresses                                       |
| tls_ca_cert_file    | string |              | /etc/openvpn/auth/ca.pem                | Path to the CA ldap backend. This must must has been pushed before                             |
| tls_cert_file       | string |              |                                         | Path to client authentication certificate                                                     |
| tls_key_file        | string |              |                                         | Path to client authentication key                                                             |
| base_dn             | string |              | ou=People,dc=example,dc=com             | Base DN where the backend will look for valid user                                             |
| search_filter       | string |              | (&(uid=%u)(accountStatus=active))       | Filter the ldap search                                                                         |
| require_group       | string | False , True |                                         | This is not an Ansible boolean but a string that will be pushed into the   configuration file, |
| group_base_dn       | string |              | ou=Groups,dc=example,dc=com             | Precise the group to look for. Required if require_group is set to   "True"                    |
| group_search_filter | string |              | ((cn=developers)(cn=artists))           | Precise valid groups                                                                           |
| verify_client_cert | string | none , optional , require | client-cert-not-required | In OpenVPN 2.4+ `client-cert-not-required` is deprecated. Use `verify-client-cert` instead. |

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
