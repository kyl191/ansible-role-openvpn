# kyl191.openvpn — Development Reference

Ansible role to install and configure OpenVPN servers. Created 2015, 1M+ downloads on Ansible Galaxy.

- **Current version:** 3.1.0
- **Minimum Ansible:** 2.13 (ansible-core)
- **Minimum OpenVPN:** 2.5
- **License:** MIT

## Directory Structure

```
tasks/                  # Modular task files
  main.yml              # Orchestrator — includes all other tasks in order
  install.yml           # Package installation (EPEL, openvpn, openssl, LDAP plugin)
  config.yml            # Server config, scripts, logrotate, CCD, systemd service
  server_keys.yml       # CA + server cert generation or import
  server_keys_crl.yml   # CRL setup (systemd timer, revoke script)
  client_keys.yml       # Client cert generation + .ovpn file creation + fetch
  revocation.yml        # Cert revocation logic
  cert_sync_detection.yml  # Diff existing certs vs openvpn_clients list
  uninstall.yml         # Full removal (stops early with meta: end_play)
  selinux.yml           # SELinux port management (semanage preferred)
  firewall/
    firewall.yml        # Auto-detect firewall from installed packages
    firewalld.yml
    iptables.yml
    ufw.yml

defaults/main/          # Role defaults (split by concern)
  openvpn.yml           # OpenVPN config: ports, ciphers, TLS, DNS, network
  role.yml              # Role behavior: dirs, fetch, firewall, CI flag, LDAP toggle
  packaging.yml         # Package names (overrideable per distro)
  ldap.yml              # LDAP auth defaults
  logrotate.yml         # Log rotation defaults

vars/                   # OS-specific overrides (loaded via with_first_found)
  os/                   # Checked first: distribution+version, then distribution, then family
  empty.yml             # Fallback (intentionally empty)
  RedHat.yml            # iptables save command override
  Debian.yml            # iptables save command override
  FreeBSD.yml           # No firewall mgmt, alternate config path
  Solaris.yml           # No logrotate, no LDAP

templates/
  server.conf.j2        # OpenVPN server config
  client.ovpn.j2        # Embedded-cert client config file
  ca.conf.j2            # OpenSSL CA config for CRL management
  ldap.conf.j2          # openvpn-auth-ldap config
  client_ccd.j2         # Per-client server-pushed options
  revoke.sh.j2          # CRL generation + cert revocation script
  crl-cron.sh.j2        # Checks CRL expiry, calls revoke.sh
  openvpn_logrotate.conf.j2
  openvpn-crl-refresh.service.j2
  selinux_module.te.j2  # Legacy SELinux TE module (being removed)

files/
  openssl-ca.ext        # X509 CA extensions
  openssl-server.ext    # X509 server extensions (TLS server EKU)
  openssl-client.ext    # X509 client extensions (TLS client EKU)
  dh.pem                # Pre-generated 2048-bit DH params (not secret)
  openvpn-server-override.conf  # systemd service override
  openvpn-crl-refresh.timer     # systemd timer (daily CRL check)

tests/
  test.yml              # Main test playbook (localhost, openvpn_ci_build: true)
  revocation-test.yml   # CRL revocation test
  ec2.yml               # E2E test against real AWS EC2 instances
  e2e_config.toml       # AWS region/profile for E2E
  *.Dockerfile          # Per-distro systemd container images
```

## Key Task Flow (`tasks/main.yml`)

1. Load OS-specific vars: `distribution+version` → `distribution` → `os_family` → `empty.yml`
2. Uninstall if `openvpn_uninstall` is truthy (exits early)
3. Install packages (`install.yml`)
4. Generate or import server keys (`server_keys.yml`)
5. Enable sysctl IP forwarding (skipped when `openvpn_ci_build`)
6. Configure firewall — auto-detect from installed packages (`openvpn_firewall: auto`)
7. Configure SELinux if enabled (`semanage` by default)
8. Cert sync detection if `openvpn_sync_certs`
9. Generate client certs/configs if `openvpn_clients` is defined
10. Revoke certs if `openvpn_use_crl` and certs to revoke exist
11. Write server config and start service (`config.yml`)

## Important Variables

### Role behavior (`defaults/main/role.yml`)
| Variable | Default | Notes |
|----------|---------|-------|
| `openvpn_clients` | `[]` | List of client names to generate certs for |
| `openvpn_base_dir` | `/etc/openvpn/server` | Server config directory |
| `openvpn_key_dir` | `{{ openvpn_base_dir }}/keys` | PKI directory |
| `openvpn_fetch_client_configs` | `true` | Fetch .ovpn files to controller |
| `openvpn_fetch_client_configs_dir` | `/tmp/ansible` | Local destination for fetched configs |
| `openvpn_firewall` | `auto` | Override: `firewalld`, `ufw`, `iptables` |
| `openvpn_manage_firewall_rules` | `true` | Set false to skip all firewall tasks |
| `openvpn_sync_certs` | `false` | Revoke certs not in `openvpn_clients` |
| `openvpn_use_crl` | `false` | Enable CRL + systemd timer |
| `openvpn_ci_build` | `false` | Skips sysctl + firewall (no kernel in CI) |
| `openvpn_uninstall` | `false` | Remove OpenVPN completely |
| `openvpn_use_ldap` | `false` | Enable LDAP authentication |

### OpenVPN config (`defaults/main/openvpn.yml`)
| Variable | Default | Notes |
|----------|---------|-------|
| `openvpn_port` | `1194` | |
| `openvpn_proto` | `udp` | |
| `openvpn_dualstack` | `true` | Appends `6` to proto for IPv4-mapped IPv6 socket |
| `openvpn_server_network` | `10.9.0.0` | |
| `openvpn_server_netmask` | `255.255.255.0` | |
| `openvpn_server_ipv6_network` | `fdbf:dd0d:1a49:2091::/64` | Fixed ULA; set null to disable IPv6 |
| `openvpn_use_tls_crypt` | `true` | Preferred; `openvpn_tls_auth_required` is deprecated |
| `openvpn_cipher` | `AES-256-GCM:AES-128-GCM:AES-256-CBC` | `data-ciphers` directive |
| `openvpn_auth_alg` | `SHA256` | HMAC (irrelevant for AEAD/GCM ciphers) |
| `openvpn_tls_version_min` | `1.2 or-highest` | |
| `openvpn_rsa_bits` | `2048` | Key size for CA, server, client certs |
| `openvpn_topology` | `subnet` | Per OpenVPN recommendation |
| `openvpn_redirect_gateway` | `def1 bypass-dhcp ipv6` | Set `''` to disable |
| `openvpn_compression` | `` (empty) | Intentionally disabled — see VORACLE attack |
| `openvpn_custom_dns` | Cloudflare + Google | Pushed to clients when `openvpn_set_dns` |
| `openvpn_addl_server_options` | `[]` | Extra lines appended to server.conf |
| `openvpn_addl_client_options` | `[]` | Extra lines in client .ovpn files |

## Variable Naming Conventions

Enforced by `.ansible-lint.yml` (production profile):

- **Public variables:** `openvpn_` prefix (e.g., `openvpn_port`)
- **Internal task variables:** `__` prefix (e.g., `__ca_cert`, `__crb_repolist`, `__ccd_contents`)
- **Loop variables:** must match `^(__|{role}_)` pattern
- **Exception:** packaging vars don't require `openvpn_` prefix (e.g., `epel_package_name`)

## Platform Support

### CI-tested (full connection test)
- Fedora 42, 43
- AlmaLinux 9, 10 / Rocky Linux 9, 10 / CentOS Stream 9, 10
- Debian 13 (trixie) / Ubuntu 24.04 (noble), 25.10 (oracular)

### CI-tested (syntax/install only, no connection test)
- AlmaLinux 8, Rocky Linux 8
- Ubuntu 22.04 (jammy), Debian 12 (bookworm)

### Community-maintained (no CI)
- FreeBSD, Solaris

## Testing

CI runs via GitHub Actions (`.github/workflows/ci.yml`):

1. `check-syntax` — `ansible-lint` (production profile)
2. `build-rhel-legacy` — AlmaLinux/Rocky 8, Docker, iptables, no tls-crypt
3. `build-debian-like` — Ubuntu 22.04/Debian 12, Docker, iptables
4. `build-systemd` — 11 modern distros, podman + full systemd, actual OpenVPN connection test + revocation test

**Local lint** (requires uv): `uv run ansible-lint`

**Container images** rebuilt weekly via `publish-*.yml` workflows, pushed to `ghcr.io/kyl191/ansible-images`.

**E2E test** (manual, AWS): configure `tests/e2e_config.toml`, then run `tests/ec2.yml`.

## Known Quirks and Constraints

- **`openvpn_ci_build: true`** skips `sysctl` and firewall tasks — no kernel access in CI containers.
- **AlmaLinux/Rocky 8** requires explicit `ansible_python_interpreter: /usr/bin/python3.9` and older ansible-core (<2.17).
- **EPEL on RHEL** installs via direct RPM URL with `disable_gpg_check: true` — this is the standard RHEL bootstrapping pattern, not a security shortcut.
- **`openvpn_compression`** is intentionally empty (disabled). Do not enable — the VORACLE attack (2018) exploits VPN compression to recover HTTPS content. See `defaults/main/openvpn.yml`.
- **`openvpn_client_register_dns`** pushes the `register-dns` option, which is Windows-specific. Benign on Linux but silently ignored.
- **Default IPv6 subnet `fdbf:dd0d:1a49:2091::/64`** is intentionally fixed across all installations. The docs explicitly discourage dynamic generation. If running multiple VPN servers on the same network, set `openvpn_server_ipv6_network` to a unique value.
- **`files/dh.pem`** is a pre-generated 2048-bit DH parameter file. DH params are not secret; this is safe and speeds up initial deployment. Set `openvpn_use_pregenerated_dh_params: true` to use it; default generates fresh params.
- **Service name** follows systemd convention: `openvpn-server@openvpn_{{ proto }}_{{ port }}.service`.
- **`tls-server`** in `server.conf.j2` is redundant when `tls-crypt` is active (OpenVPN 2.5+ implies it), but harmless.
- **SELinux:** `openvpn_selinux_use_semanage: true` is the default (uses `semanage port`). The legacy compiled SELinux module path (`openvpn_selinux_use_semanage: false`) is being deprecated.
- **Firewall detection** is based on installed packages (via `package_facts`), not command availability. Requires `python3-dnf` or `python3-apt`. Set `openvpn_firewall` explicitly if detection fails.

## Required Ansible Collections

```yaml
# requirements.yml
collections:
  - ansible.posix    # seport, sysctl, firewalld modules
  - community.general  # ufw module
```

Install with: `ansible-galaxy collection install -r requirements.yml`

## Certificate Management Notes

- Certs generated with `openssl` CLI commands (not community.crypto) — no extra collection needed
- CA, server, and client certs all default to 3650-day validity (hardcoded in task files)
- CRL is optional (`openvpn_use_crl: false`); when enabled, a systemd timer refreshes it daily
- `openvpn_sync_certs: true` detects clients in `openvpn_key_dir` not in `openvpn_clients` and revokes them
- Client `.ovpn` files embed CA cert, client cert, client key, and tls-crypt key inline
