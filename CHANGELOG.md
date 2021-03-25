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

Since this playbook is only on Linux, which supports IPv4 connections on IPv6 sockets, the server config is now IPv6 by default (https://github.com/OpenVPN/openvpn/blob/master/README.IPv6#L50), by means of using `{{ openvpn_proto }}6` in the server template. Specifying a `*6` protocol for `openvpn_proto` is now an error, and will cause OpenVPN to fail to start.
