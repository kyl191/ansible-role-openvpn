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
  validate.yml           # Fail-fast checks (CN length; both-address-families-empty) — always runs first
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
    firewall.yml        # Dispatcher: resolves __openvpn_firewall_backend once, then includes
                         # the matching backend file; also hosts the IPv4/IPv6 default-route guards
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

1. Validate variables (`validate.yml`) — CN length, and (once the stashed work lands) that at
   least one address family is enabled. Tagged `always`, runs before OS vars are even loaded.
2. Load OS-specific vars: `distribution+version` → `distribution` → `os_family` → `empty.yml`
3. Uninstall if `openvpn_uninstall` is truthy (exits early)
4. Install packages (`install.yml`)
5. Generate or import server keys (`server_keys.yml`)
6. Enable sysctl IP forwarding (skipped when `openvpn_ci_build`)
7. Configure firewall — dispatcher resolves the backend once, guards against a missing
   default route, then includes the matching backend file (see "Firewall Architecture" below)
8. Configure SELinux if enabled (`semanage` by default)
9. Cert sync detection if `openvpn_sync_certs`
10. Generate client certs/configs if `openvpn_clients` is defined
11. Revoke certs if `openvpn_use_crl` and certs to revoke exist
12. Write server config and start service (`config.yml`)

## Firewall Architecture

`tasks/firewall/firewall.yml` is the dispatcher. It resolves which backend to use into
`__openvpn_firewall_backend` (`iptables`/`firewalld`/`ufw`) once, then includes the matching
`tasks/firewall/{iptables,firewalld,ufw}.yml`. Backend selection: `openvpn_firewall` var if not
`auto`, else whichever of firewalld/ufw/iptables is installed
(`ansible_facts['packages']` — bracket notation deliberately, for consistency with the rest of
the role rather than relying on Jinja's dot-access sugar).

`firewall.yml` also hosts two `ansible.builtin.fail` guards (IPv4 and IPv6) that run before
dispatch, catching the case where SNAT mode needs a source address but the host has no default
route for that family. They only apply to `iptables`/`ufw` in SNAT mode — firewalld doesn't need
a known address at all (uses its own configured default zone, overridable via
`openvpn_firewalld_zone`, not an interface/route lookup), and MASQUERADE mode picks the address
at packet-send time. Do not duplicate these guards back into `iptables.yml`/`ufw.yml` — that was
the state before this was consolidated, and led to the same check being maintained twice.

## Important Variables

Defaults are split by concern and mostly self-explanatory single-line `openvpn_x: value`
assignments — read the files directly rather than a transcribed copy here, which would go stale
the moment a default changes:

- **`defaults/main/role.yml`** — role behavior: client list, directories, config-fetch settings,
  firewall toggles, CI/uninstall/LDAP flags.
- **`defaults/main/openvpn.yml`** — OpenVPN server config: ports, ciphers, TLS, DNS, network,
  CN generation.
- **`defaults/main/packaging.yml`**, **`ldap.yml`**, **`logrotate.yml`** — narrower, per their name.

The handful of variables with non-obvious rationale (not just "what", but "why") are called out
inline in the files themselves as comments, and echoed here since they're easy to miss:

- **`openvpn_snat_source_ipv4`/`openvpn_snat_source_ipv6`** — SNAT source for iptables/ufw (SNAT
  mode only, not needed for MASQUERADE or firewalld). Renamed from `openvpn_lan_source_ip`
  (breaking change) since it isn't necessarily a LAN address. Override to avoid depending on
  `ansible_facts['default_ipv4'/'default_ipv6']` at all.
- **`openvpn_firewalld_zone`** (`auto`) — autodetects firewalld's configured default zone;
  override (e.g. `public`) to skip autodetection.
- **`openvpn_ca_cn`/`openvpn_server_cn`/`openvpn_client_cn_prefix`** — pre-truncate
  `inventory_hostname` so prefix + hostname stays within X.509's 64-char CN limit. Adjust the
  truncation length if you override the prefix.
- **`openvpn_compression`** — intentionally left empty/disabled. The VORACLE attack (2018)
  exploits VPN compression to recover HTTPS plaintext. Do not enable.
- **`openvpn_redirect_gateway`** (`def1 bypass-dhcp ipv6`) — intentionally NOT conditional on
  which address families are enabled; see kill-switch rationale below.
- **`openvpn_server_network`** — intended to become optional (set `""` to disable the IPv4
  tunnel, symmetric with `openvpn_server_ipv6_network: ""`). Template-side gating for this is
  still in `git stash@{0}`, not yet committed — see next section.

## Dual-Stack: IPv4 and IPv6 Are Both Optional

Goal: `openvpn_server_network: ""` disables the IPv4 tunnel entirely, symmetric with
`openvpn_server_ipv6_network: ""` (which already worked). At least one must be set.

**Status on this branch:** the SNAT-source variables (`openvpn_snat_source_ipv4/ipv6`), the
`openvpn_firewalld_zone` var, and the `firewall.yml` dispatcher + route guards described above
are committed. Still uncommitted, sitting in `git stash@{0}` ("v4v6 mixed support"):

- `tasks/validate.yml` failing fast when both `openvpn_server_network` and
  `openvpn_server_ipv6_network` are empty.
- `templates/server.conf.j2` gating the `server` line on `openvpn_server_network` being
  non-empty (matching how `server-ipv6` already works).
- Each firewall backend's NAT rules gated on `openvpn_server_network` being non-empty.
- `tasks/main.yml`'s IPv4 forwarding sysctl gated on `openvpn_server_network` being non-empty,
  and the IPv6 sysctl's `when:` fixed from a bare-truthy chain to `| length > 0` (see testing
  gotchas below for why the bare form breaks).
- A new `test-no-default-route` CI job with four scenarios (guard fires / disabled-family works,
  for each of IPv4 and IPv6) drafted into `.github/workflows/ci.yml`.

Apply the stash before relying on any of the above as working behavior.

**`openvpn_redirect_gateway` is intentionally NOT conditional on which families are enabled.** It
always pushes both `def1 bypass-dhcp` (IPv4) and `ipv6` flags, even when one family's tunnel is
disabled. This is deliberate kill-switch behavior: redirecting a disabled family's default
gateway into a tunnel that can't carry it black-holes that traffic on the client instead of
leaking it out over the client's local connection. Don't "fix" this into a computed/conditional
default — it was considered and explicitly rejected.

## Variable Naming Conventions

Enforced by `.ansible-lint.yml` (production profile):

- **Public variables:** `openvpn_` prefix (e.g., `openvpn_port`)
- **Internal task variables:** `__` prefix (e.g., `__ca_cert`, `__crb_repolist`, `__ccd_contents`)
- **Loop variables:** must match `^(__|{role}_)` pattern
- **Exception:** packaging vars don't require `openvpn_` prefix (e.g., `epel_package_name`)

## Platform Support

### CI-tested (full connection test)

- Fedora 43, 44
- AlmaLinux 9, 10 / Rocky Linux 9, 10 / CentOS Stream 9, 10
- Debian 13 (trixie) / Ubuntu 24.04 (noble), 26.04

### CI-tested (syntax/install only, no connection test)

- AlmaLinux 8, Rocky Linux 8
- Ubuntu 22.04 (jammy)

### Community-maintained (no CI)

- FreeBSD, Solaris

## Testing

CI runs via GitHub Actions (`.github/workflows/ci.yml`):

1. `check-syntax` — `ansible-lint` (production profile)
2. `build-rhel-legacy` — AlmaLinux/Rocky 8, Docker, iptables, no tls-crypt
3. `build-debian-like` — Ubuntu 22.04/Debian 12, Docker, iptables
4. `build-systemd` — 11 modern distros, podman + full systemd, actual OpenVPN connection test + revocation test
5. `test-no-default-route` — removes the container's real default IPv4 route to genuinely exercise the missing-route guards, rather than mocking the fact

**Local lint** (requires uv): `uv run ansible-lint`

**Container images** rebuilt weekly via `publish-*.yml` workflows, pushed to `ghcr.io/kyl191/ansible-images`.

**E2E test** (manual, AWS): configure `tests/e2e_config.toml`, then run `tests/ec2.yml`.

### Gotchas when writing firewall/fact-related tests

- **`ansible_facts | combine(...)` reassigned via `set_fact: ansible_facts: ...` is broken on
  ansible-core 2.19+.** It degrades `ansible_facts` from Ansible's special mapping type to a
  plain dict; later modules that return partial `ansible_facts` (e.g. `package_facts`) then fail
  to merge back in correctly — `ansible_facts['packages']` throws `AttributeError` even with
  bracket notation. Don't use this to mock a "missing default route" in tests. Instead, do the
  real thing: run the role once with `openvpn_manage_firewall_rules: false` (or
  `openvpn_no_nat: true` if you still want firewall package installs to happen), then
  `ip route del default` for real, then re-run with `--tags firewall,validate` so gathered facts
  genuinely reflect the missing route.

- **Never run `ip route del` (or similar) in a podman container without first confirming it's
  actually network-isolated.** An unspecified `podman run --network` combined with
  `--privileged --cgroupns=host` can end up sharing the host's real network interface. Always
  pass `--network=podman` (or another named bridge) explicitly and verify with
  `ip -brief addr show` inside the container (expect an isolated bridge-style address, never the
  host's real interface name) before doing anything destructive to routing/networking in there.

- **The bare-truthy `when:` idiom `X is defined and X and ...` breaks on newer ansible-core when
  `X` is `""`.** `and` short-circuits and returns the operand, not a coerced bool, and current
  ansible-core enforces that `when:` results must be actual booleans
  ("Conditional result... must have a boolean result"). Use
  `X is defined and X | length > 0 and ...` (or `is truthy`/`is falsy`) instead.

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
