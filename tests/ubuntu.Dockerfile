ARG VERSION=latest
FROM docker.io/library/ubuntu:$VERSION
LABEL org.opencontainers.image.source=https://github.com/kyl191/ansible-role-openvpn
LABEL org.opencontainers.image.title="Ubuntu $VERSION with Ansible"
LABEL org.opencontainers.image.description="Ubuntu with Ansible, based off https://github.com/diodonfrost/docker-ansible/blob/master/debian-ansible/Dockerfile.debian-testing"

ENV container docker
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git \
    ansible \
    apt-transport-https \
    ca-certificates-java \
    curl \
    init \
    openssh-server openssh-client \
    unzip \
    rsync \
    sudo \
    fuse snapd snap-confine squashfuse \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ansible \
    firewalld python3-firewall \
    python3-apt \
    && rm -rf /var/lib/apt/lists/*

# Configure udev for docker integration
RUN dpkg-divert --local --rename --add /sbin/udevadm && ln -s /bin/true /sbin/udevadm

RUN mkdir /etc/ansible && echo "[local]\nlocalhost ansible_connection=local" > /etc/ansible/hosts

CMD ["/sbin/init"]
