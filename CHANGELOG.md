# Version 3.0 (2024-12-26)

## Updated to latest Ansible recommendations

ansible-lint isn't complaining anymore. It's also added to the CI system so the role shouldn't regress. A few more changes:

* I've converted to using `truthy/falsy` instead of inconsistenly using `|bool` for boolean comparisons.
* Variables are prefixed with `openvpn_` to make sure they are isolated to this role. (There are [limited exceptions](.ansible-lint.yml))
Notable variable changes include:
  * `ldap` dict becoming `openvpn_ldap`
  * `tls_auth_required` becoming `openvpn_tls_auth_required`

## Changed Supported OS Versions

Actually supported - I make sure an OpenVPN connection works before putting up a Ansible Galaxy release:

* Fedora 38+ ([OpenVPN 2.6](https://packages.fedoraproject.org/pkgs/openvpn/openvpn/))
* CentOS Stream 9/AlmaLinux/Rocky/RHEL 9+ ([OpenVPN 2.5](https://packages.fedoraproject.org/pkgs/openvpn/openvpn/))

Kind of supported - CI does sanity checks:

* Ubuntu 22.04+ ([OpenVPN 2.5](https://launchpad.net/ubuntu/+source/openvpn), [list of distro releases](https://wiki.ubuntu.com/Releases))
* Debian 12 ([OpenVPN 2.6](https://packages.debian.org/search?keywords=openvpn), [list of distro releases](https://www.debian.org/releases/))

Community contributions - no automated checks, they might work:

* FreeBSD
* Solaris

Older OSes might work - there's no explicit blocking, but workarounds will be removed with EOLed OSes to simplify the role.

### Removed Workarounds

* CentOS 6 - no longer [ignore errors when setting sysctls](http://serverfault.com/questions/477718/sysctl-p-etc-sysctl-conf-returns-error)
* CentOS 7 - Potentially affected by defaulting service name to the systemd style
* Fedora <33 - no longer installing `python2-dnf` and `python2-firewalld` for Ansible to run on Python 2.

### RHEL-alike 8 notes

Known issue: RHEL-alike 8 [can't manage packages using ansible-core >=2.17.0](https://github.com/ansible/ansible/issues/82068#issuecomment-2123567229), you will need to use an earlier version of Ansible.

Other notes on RHEL-alike 8 variants:

* AlmaLinux 8 and Rocky Linux 8 need an out-of-band python upgrade with `dnf install python3.9` and setting the `ansible_python_interpreter` value to `/usr/bin/python3.9`
* CentOS 8 and CentOS Stream 8 packages were vaulted ([CentOS 8 announcement](https://www.centos.org/centos-linux-eol/), [Stream 8 announcement](https://blog.centos.org/2023/04/end-dates-are-coming-for-centos-stream-8-and-centos-linux-7/)), which breaks Yum downloading packages

## Assuming OpenVPN 2.5+

Biggest change (as far as I can tell) is OpenVPN deprecated `cipher` and replaced it with `data-cipher`. All the supported OSes are OpenVPN2.5+, so I've updated the server config to use `data-cipher` when `openvpn_cipher` is set.

If the event you need fallback support on the server for older clients, set the value `data-ciphers-fallback` through the playbook option `openvpn_addl_server_options`.

If you're forced to use OpenVPN 2.4 or earlier, this should work:

* Unset `openvpn_cipher` in your vars file, eg `openvpn_cipher: ~`
* Include `cipher` in `openvpn_addl_server_options`, eg `openvpn_addl_server_options: ["cipher AES-256-CBC"]`

Similarly on the client, you can use `openvpn_addl_client_options` to set `cipher` if needed.

Discussion in [this issue](https://github.com/kyl191/ansible-role-openvpn/issues/196).

## LDAP plugin no longer built by default

This thing has honestly made me nervous since merging it because it's rather complicated. [A compliation issue was reported](https://github.com/kyl191/ansible-role-openvpn/issues/174), and Fedora/EPEL, Debian, and Ubuntu all provide packages for openvpn-auth-ldap so I'm dropping the compilation step to simplify the role.

* Fedora/EPEL: <https://packages.fedoraproject.org/pkgs/openvpn-auth-ldap/openvpn-auth-ldap/index.html>
* Debian: <https://packages.debian.org/search?keywords=openvpn-auth-ldap>
* Ubuntu: <https://launchpad.net/ubuntu/+source/openvpn-auth-ldap>

If you need it and there's no prebuilt package, manually build & configure it yourself and set the variable `openvpn_use_prebuilt_ldap_plugin` to False to skip the failing package install.

## systemd by default

CentOS 8+, Ubuntu 22.04 and Debian 12 are all using systemd service units, so I've updated the default `openvpn_service_name` to be systemd style. I've kept the existing `openvpn` value for Solaris and FreeBSD, hopefully it works there.

The CRL crontab is also replaced by a systemd timer.

## Future changes

### Dropping iptables in a future release

Fedora/CentOS use firewalld and Debian [recommends firewalld](https://wiki.debian.org/nftables#Use_firewalld). Ubuntu is alone in [using ufw](https://documentation.ubuntu.com/server/how-to/security/firewalls/)

nftables is the replacement for iptables, [firewalld uses nftables as the default backend](https://firewalld.org/2018/07/nftables-backend). Unfortunately there is [no nftables support in Ansible yet](https://forum.ansible.com/t/is-there-an-official-or-defacto-nftables-module-or-collection/7023), so I'm choosing to drop iptables and suggest firewalld as the replacement.

This will realistically be done when `iptables` starts requiring more maintenance than it does right now.

### Help wanted: Develop end to end testing

The Fedora & CentOS connection testing is currently manual. (This is also blocking the better testing of the Debian & Ubuntu distros).

### Switch to testing against multiple `ansible-core` releases

The CI currently tests the role on AlmaLinux/Rocky Linux 8 using the highest supported Ansible version for [CentOS 8 as a control node - ansible-8.7.0/ansible-core-2.15.13](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix), but this is limited to running the playbook without attempting an OpenVPN connection.

This is partly because the minimum ansible-core version for this role is 2.11 - if this run starts failing, I'll know the minimum ansible-core version will need to be bumped.

At some point in the future I'll switch to testing different ansible-core versions explicitly, and drop the RHEL-alike 8 CI builds at that time.

# Version 2.0 (2016-04-11)

## Improving TLS Security

1. Added `auth SHA256` so MACs on the individual packets are done with SHA256 instead of SHA1.

2. Added `tls-version-min 1.2` to drop SSL3 + TLS v1.0 support. This breaks older clients (2.3.2+), but those versions have been out for a while.

3. Restricted the `tls-cipher`s allowed to a subset of Mozilla's modern cipher list + DHE for older clients. ECDSA support is included for when ECDSA keys can be used.

4. New keys are 2048 bit by default, downgraded from 4096 bit. This is based on Mozilla's SSL guidance, combined with the expectation of being able to use ECDSA keys in a later revision of this playbook.

5. As part of the move to 2048 bit keys, the 4096 bit DH parameters are no longer distributed. It was originally distributed since generating it took ~75 minutes, but the new 2048 bit parameters take considerably less time.

Points 2 & 3 are gated by the `openvpn_use_modern_tls` variable, which defaults to `true`.

## Adding Cert Validations

OpenVPN has at least two kinds of certification validation available: (Extended) Key Usage checks, and certificate content validation.

### EKU

Previously only the client was verifying that the server cert had the correct usage, now the verification is bi-directional.

### Certificate content

Added the ability to verify the common name that is part of each certificate. This required changing the common names that each certificate is generated with, which means that the ability to wipe out the existing keys was added as well.

Again, both these changes are gated by a variable (`openvpn_verify_cn`). Because this requires rather large client changes, it is off by default.

## Wiping out & reinstalling

Added the ability to wipe out & reinstall OpenVPN. Currently it leaves firewall rules behind, but other than that everything is removed.

Use `ansible-playbook -v openvpn.yml --extra-vars="openvpn_uninstall=true" --tags uninstall` to just run the uninstall portion.

## Connect over IPv6

Previously, you had to explicitly use `udp6` or `tcp6` to use IPv6. OpenVPN isn't dual stacked if you use plain `udp`/`tcp`, which results in being unable to connect to the OpenVPN server if it has an AAAA record, on your device has a functional IPv6 connection, since the client will choose which stack to use if you just use plain `udp`/`tcp`.

Since this playbook is only on Linux, which supports IPv4 connections on IPv6 sockets, the server config is now IPv6 by default (<https://github.com/OpenVPN/openvpn/blob/master/README.IPv6#L50>), by means of using `{{ openvpn_proto }}6` in the server template. Specifying a `*6` protocol for `openvpn_proto` is now an error, and will cause OpenVPN to fail to start.
