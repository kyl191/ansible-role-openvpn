OpenVPN
=========
Github Actions (PRs & mainline): ![Github CI](https://github.com/kyl191/ansible-role-openvpn/workflows/CI/badge.svg)

Travis CI (Actually launching openvpn): [![Build Status](https://travis-ci.org/kyl191/ansible-role-openvpn.svg?branch=master)](https://travis-ci.org/kyl191/ansible-role-openvpn)

This role installs OpenVPN, configures it as a server, sets up networking and firewalls (primarily firewalld, ufw and iptables are best effort), and can optionally create client certificates.

OSes in CI build:
- Fedora 32+
- CentOS 7 & 8

Note: I am providing code in the repository to you under an open source license. Because this is my personal repository, the license you receive to my code is from me and not my employer.

# Requirements
OpenVPN must be available as a package in yum/dnf/apt! For CentOS users, this role will run `yum install epel-release` to ensure openvpn is available.

Ubuntu precise has a [weird bug](https://bugs.launchpad.net/ubuntu/+source/iptables-persistent/+bug/1002078) that might make the iptables-persistent install fail. There is a [workaround](https://forum.linode.com/viewtopic.php?p=58233#p58233).

## Ansible 2.10 and higher
With the release of Ansible 2.10, modules have been moved into collections. With the exception of ansible.builtin modules, this means additonal collections must be installed in order to use modules such as seboolean (now ansible.posix.seboolean). This collections is now required: `ansible.posix` and this collection is required if using ufw: `community.general`. Installing the collections:

```
ansible-galaxy collection install ansible.posix
ansible-galaxy collection install community.general
```

# Support Notes/Expectations
I personally use this role to manage OpenVPN on CentOS 8. I try to keep the role on that platform fully functional with the default config.
Please recognise that I am a single person, and I have a full time job and other commitments.

Responses to any issues will be on a best effort basis on my part, including the possibility that I don't respond at all.
Issues arising from use of the non-defaults (including any of the major community contributions) will be deprioritized.

Major community contributions:
* Functionality to revoke certs
* All of the LDAP support

# Role Variables
## Role options
These options change how the role works. This is a catch-all group, specific groups are broken out below.
| Variable                     | Type    | Choices     | Default           | Comment                                                                       |
|------------------------------|---------|-------------|-------------------|-------------------------------------------------------------------------------|
| clients                      | list    |             | []                | List of clients to add to OpenVPN                                             |
| openvpn_base_dir             | string  |             | /etc/openvpn      | Path where your OpenVPN config will be stored                                 |
| openvpn_client_config_no_log | boolean | true, false | true              | Prevent client configuration files to be logged to stdout by Ansible          |
| openvpn_key_dir              | string  |             | /etc/openvpn/keys | Path where your server private keys and CA will be stored                     |
| openvpn_ovpn_dir             | string  |             | /etc/openvpn      | Path where your client configurations will be stored                          |
| openvpn_revoke_these_certs   | list    |             | []                | List of client certificates to revoke.                                        |
| openvpn_selinux_module       | string  |             | my-openvpn-server | Set the SELinux module name                                                   |
| openvpn_service_name         | string  |             | openvpn           | Name of the service. Used by systemctl to start the service                   |
| openvpn_sync_certs           | boolean | true, false | false             | Revoke certificates not explicitly defined in 'clients'                       |
| openvpn_uninstall            | boolean | true, false | false             | Set to true to uninstall the OpenVPN service                                  |
| openvpn_use_ldap             | boolean | true, false | false             | Active LDAP backend for authentication. Client certificate not needed anymore |
### Config fetching
Change these options if you need to adjust how the configs are download to your local system
| Variable                            | Type    | Choices     | Default      | Comment                                                                                                                                   |
|-------------------------------------|---------|-------------|--------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_fetch_client_configs        | boolean | true, false | true         | Download generated client configurations to the local system                                                                              |
| openvpn_fetch_client_configs_dir    | string  |             | /tmp/ansible | If openvpn_fetch_client_configs is true, the local directory to download the client config files into                                     |
| openvpn_fetch_client_configs_suffix | string  |             | ""           | If openvpn_fetch_client_configs is true, the suffix to append to the downloaded client config files before the trailing `.ovpn` extension |

### Firewall
Change these options if you need to force a particular firewall or change how the playbook interacts with the firewall.
| Variable                         | Type    | Choices                        | Default  | Comment                                                                                                     |
|----------------------------------|---------|--------------------------------|----------|-------------------------------------------------------------------------------------------------------------|
| firewalld_default_interface_zone | string  |                                | public   | Firewalld zone where the "ansible_default_ipv4.interface" will be pushed into                               |
| iptables_service                 | string  |                                | iptables | Override the iptables service name                                                                          |
| manage_firewall_rules            | boolean | true, false                    | true     | Allow playbook to manage iptables                                                                           |
| openvpn_firewall                 | string  | auto, firewalld, ufw, iptables | auto     | The firewall software to configure network rules. "auto" will attempt to detect it by inspecting the system |
| openvpn_masquerade_not_snat      | boolean | true, false                    | false    | Set to true if you want to set up MASQUERADE instead of the default SNAT in iptables.                       |
## OpenVPN Config Options
These options change how OpenVPN itself works.
### Networking
| Variable                    | Type         | Choices           | Default                    | Comment                                                                                                                                              |
|-----------------------------|--------------|-------------------|----------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_client_register_dns | boolean      | true, false       | true                       | Add `register-dns` option to client config (Windows only).                                                                                           |
| openvpn_client_to_client    | boolean      | true, false       | false                      | Set to true if you want clients to access each other.                                                                                                |
| openvpn_custom_dns          | list[string] |                   | []                         | List of DNS servers, only applied if `openvpn_set_dns` is set to true                                                                                |
| openvpn_dualstack           | boolean      |                   | true                       | Whether or not to use a dualstack (IPv4 + v6) socket                                                                                                 |
| openvpn_keepalive_ping      | int          |                   | 5                          | Set `keepalive` ping interval seconds.                                                                                                               |
| openvpn_keepalive_timeout   | int          |                   | 30                         | Set `keepalive` timeout seconds                                                                                                                      |
| openvpn_local               | string       |                   | `unset`                    | Local host name or IP address for bind.  If specified, OpenVPN will bind to this address only.  If unspecified, OpenVPN will bind to all interfaces. |
| openvpn_port                | int          |                   | 1194                       | The port you want OpenVPN to run on. If you have different ports on different servers, I suggest you set the port in your inventory file.            |
| openvpn_proto               | string       | udp, tcp          | udp                        | The protocol you want OpenVPN to use                                                                                                                 |
| openvpn_redirect_gateway    | boolean      | true, false       | true                       | OpenVPN gateway push                                                                                                                                 |
| openvpn_resolv_retry        | int/string   | any int, infinite | 5                          | Hostname resolv failure retry seconds. Set "infinite" to retry indefinitely in case of poor connection or laptop sleep mode recovery etc.            |
| openvpn_server_hostname     | string       |                   | `{{ inventory_hostname }}` | The server name to place in the client configuration file                                                                                            |
| openvpn_server_ipv6_network | string       |                   | `unset`                    | If set, the network address and prefix of an IPv6 network to assign to clients. If True, IPv4 still used too.                                        |
| openvpn_server_netmask      | string       |                   | 255.255.255.0              | Netmask of the private network                                                                                                                       |
| openvpn_server_network      | string       |                   | 10.9.0.0                   | Private network used by OpenVPN service                                                                                                              |
| openvpn_set_dns             | boolean      | true, false       | true                       | Will push DNS to the client (Cloudflare and Google)                                                                                                  |
| openvpn_tun_mtu             | int          |                   | `unset`                    | Set `tun-mtu` value. Empty for default.                                                                                                              |
### Security
| Variable                           | Type    | Choices     | Default     | Comment                                                                                                                                                         |
|------------------------------------|---------|-------------|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_auth_alg                   | string  |             | SHA256      | Set `auth` authentication algoritm.                                                                                                                             |
| openvpn_ca_key                     | dict    |             | `unset`     | Contain "crt" and "key". If not set, CA cert and key will be automatically generated on the target system.                                                      |
| openvpn_cipher                     | string  |             | AES-256-CBC | Set `cipher` option for server and client.                                                                                                                      |
| openvpn_crl_path                   | string  |             | `unset`     | Define a path to the CRL file for server revocation check.                                                                                                      |
| openvpn_duplicate_cn               | boolean | true, false | false       | Add `duplicate-cn` option to server config - this allows clients to connect multiple times with the one key. NOTE: client ip addresses won't be static anymore! |
| openvpn_rsa_bits                   | int     |             | 2048        | Number of bits used to protect generated certificates                                                                                                           |
| openvpn_script_security            | int     |             | 1           | Set openvpn script security option                                                                                                                              |
| openvpn_tls_auth_key               | string  |             | `unset`     | Single item with a pre-generated TLS authentication key.                                                                                                        |
| openvpn_use_crl                    | boolean | true, false | false       | Configure OpenVPN server to honor certificate revocation list.                                                                                                  |
| openvpn_use_hardened_tls           | boolean | true, false | true        | Require a minimum version of TLS 1.2                                                                                                                            |
| openvpn_use_modern_tls             | boolean | true, false | true        | Use modern Cipher for TLS encryption (Not recommended with OpenVPN 2.4)                                                                                         |
| openvpn_use_pregenerated_dh_params | boolean | true, false | false       | DH params are generted with the install by default                                                                                                              |
| openvpn_verify_cn                  | boolean | true, false | false       | Check that the CN of the certificate match the FQDN                                                                                                             |
| tls_auth_required                  | boolean | true, false | true        | Ask the client to push the generated ta.key of the server during the connection                                                                                 |
### Operations
| Variable                           | Type    | Choices     | Default                                          | Comment                                                                                                                                                                       |
|------------------------------------|---------|-------------|--------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| openvpn_addl_client_options        | list    |             | empty                                            | List of user-defined client options that are not already present in the client template. (e.g. `- mssfix 1400`)                                                               |
| openvpn_addl_server_options        | list    |             | empty                                            | List of user-defined server options that are not already present in the server template. (e.g. `- ping-timer-rem`)                                                            |
| openvpn_compression                | string  |             | lzo                                              | Set `compress` compression option. Empty for no compression.                                                                                                                  |
| openvpn_config_file                | string  |             | openvpn_{{ openvpn\_proto }}\_{{ openvpn_port }} | The config file name you want to use (set in vars/main.yml)                                                                                                                   |
| openvpn_enable_management          | boolean | true, false | false                                            |                                                                                                                                                                               |
| openvpn_ifconfig_pool_persist_file | string  |             | ipp.txt                                          |                                                                                                                                                                               |
| openvpn_management_bind            | string  |             | /var/run/openvpn/management unix                 | The interface to bind on for the management interface. Can be unix or TCP socket.                                                                                             |
| openvpn_management_client_user     | string  |             | root                                             | Use this user when using a Unix socket for management interface.                                                                                                              |
| openvpn_push                       | list    |             | empty                                            | Set here a list of string that will be inserted into the config file as `push ""`. E.g `- route 10.20.30.0 255.255.255.0` will generate push "route 10.20.30.0 255.255.255.0" |
| openvpn_script_client_connect      | string  |             | `unset`                                          | Path to your openvpn client-connect script                                                                                                                                    |
| openvpn_script_client_disconnect   | string  |             | `unset`                                          | Path to your openvpn client-disconnect script                                                                                                                                 |
| openvpn_script_down                | string  |             | `unset`                                          | Path to your openvpn down script                                                                                                                                              |
| openvpn_script_up                  | string  |             | `unset`                                          | Path to your openvpn up script                                                                                                                                                |
| openvpn_service_group              | string  |             | nogroup                                          | Set the openvpn service group.                                                                                                                                                |
| openvpn_service_user               | string  |             | nobody                                           | Set the openvpn service user.                                                                                                                                                 |
| openvpn_status_version             | int     | 1, 2, 3     | 1                                                | Define the formatting of the openvpn-status.log file where are listed current client connection                                                                               |
| openvpn_topology                   | string  |             | `unset`                                          | the "topology" keyword will be set in the server config with the specified value.                                                                                             |

### OpenVPN custom client config (server pushed)
| Variable                  | Type    | Choices | Default | Comment                                              |
|---------------------------|---------|---------|---------|------------------------------------------------------|
| openvpn_client_config     | Boolean |         | false   | Set to true if enable client configuration directory |
| openvpn_client_config_dir | string  |         | ccd     | Path of `client-config-dir`                          |
| openvpn_client_configs    | dict    |         | {}      | Dict of settings custom client configs               |

## Logrotate
Set your own custom logrotate options
| Variable                 | Type   | Choices | Default                                                                                                     | Comment                                                                                          |
|--------------------------|--------|---------|-------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| openvpn_log_dir          | string |         | /var/log                                                                                                    | Set location of openvpn log files. This parameter is a part of `log-append` configuration value. |
| openvpn_log_file         | string |         | openvpn.log                                                                                                 | Set log filename. This parameter is a part of `log-append` configuration value.                  |
| openvpn_logrotate_config | string |         | rotate 4<br />weekly<br />missingok<br />notifempty<br />sharedscripts<br />copytruncate<br />delaycompress | Configure logrotate script.                                                                      |

## Packaging
This role pulls in a bunch of different packages. Override the names as necessary.
| Variable                         | Type   | Choices | Default             | Comment                                                                     |
|----------------------------------|--------|---------|---------------------|-----------------------------------------------------------------------------|
| epel_package_name                | string |         | epel-release        | Name of the epel-release package to install from the package manager        |
| iptables_persistent_package_name | string |         | iptables-persistent | Name of the iptables-persistent package to install from the package manager |
| iptables_services_package_name   | string |         | iptables-services   | Name of the iptables-services package to install from the package manager   |
| openssl_package_name             | string |         | openssl             | Name of the openssl package to install from the package manager             |
| openvpn_ldap_plugin_package_name | string |         | openvpn-auth-ldap   | Name of the openvpn-auth-ldap package to install from the package manager   |
| openvpn_package_name             | string |         | openvpn             | Name of the openvpn package to install from the package manager             |
| python_firewall_package_name     | string |         | python-firewall     | Name of the python-firewall package to install from the package manager     |

## LDAP object
| Variable            | Type   | Choices                   | Default                                 | Comment                                                                                      |
|---------------------|--------|---------------------------|-----------------------------------------|----------------------------------------------------------------------------------------------|
| ldap                | dict   |                           |                                         | Dictionary that contain LDAP configuration                                                   |
| url                 | string |                           | ldap://host.example.com                 | Address of you LDAP backend with syntax ldap[s]://host[:port]                                |
| anonymous_bind      | string | False , True              | False                                   | This is not an Ansible boolean but a string that will be pushed into the configuration file. |
| bind_dn             | string |                           | uid=Manager,ou=People,dc=example,dc=com | Bind DN used if "anonymous_bind" set to "False"                                              |
| bind_password       | string |                           | mysecretpassword                        | Password of the bind_dn user                                                                 |
| tls_enable          | string | yes , no                  | no                                      | Force TLS encryption. Not necessary with ldaps addresses                                     |
| tls_ca_cert_file    | string |                           | /etc/openvpn/auth/ca.pem                | Path to the CA ldap backend. This must have been pushed before                               |
| tls_cert_file       | string |                           |                                         | Path to client authentication certificate                                                    |
| tls_key_file        | string |                           |                                         | Path to client authentication key                                                            |
| base_dn             | string |                           | ou=People,dc=example,dc=com             | Base DN where the backend will look for valid user                                           |
| search_filter       | string |                           | (&(uid=%u)(accountStatus=active))       | Filter the ldap search                                                                       |
| require_group       | string | False , True              |                                         | This is not an Ansible boolean but a string that will be pushed into the configuration file. |
| group_base_dn       | string |                           | ou=Groups,dc=example,dc=com             | Precise the group to look for. Required if require_group is set to "True"                    |
| group_search_filter | string |                           | ((cn=developers)(cn=artists))           | Precise valid groups                                                                         |
| verify_client_cert  | string | none , optional , require | client-cert-not-required                | In OpenVPN 2.4+ `client-cert-not-required` is deprecated. Use `verify-client-cert` instead.  |

# Dependencies
Does not depend on any other roles

# Example Playbook
```
- hosts: vpn
  gather_facts: true
  roles:
    - role: kyl191.openvpn
      openvpn_port: 4300
      openvpn_sync_certs: true
      clients:
        - client1
        - client2
```

> **Note:** As the role will need to know the remote used platform (32 or 64 bits), you must set `gather_facts` to `true` in your play.

# License
MIT

# Author Information
Written by Kyle Lexmond
