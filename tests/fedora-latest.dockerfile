FROM fedora:latest
RUN dnf install -y systemd && dnf clean all
