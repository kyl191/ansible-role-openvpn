ARG VERSION=latest
FROM docker.io/library/debian:$VERSION
LABEL org.opencontainers.image.source=https://github.com/kyl191/ansible-role-openvpn
LABEL org.opencontainers.image.title="Debian $VERSION with Ansible"
LABEL org.opencontainers.image.description="Debian with Ansible, based off https://github.com/diodonfrost/docker-ansible/blob/master/debian-ansible/Dockerfile.debian-testing"

ENV container docker
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    git \
    python3-pip \
    gnupg2 \
    dirmngr \
    apt-transport-https \
    curl \
    init \
    openssh-server openssh-client \
    systemd \
    unzip \
    rsync \
    sudo \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ansible \
    firewalld python3-firewall \
    python3-apt \
    && rm -rf /var/lib/apt/lists/*

RUN dpkg-divert --local --rename --add /sbin/udevadm && ln -s /bin/true /sbin/udevadm

RUN mkdir -p /etc/ansible && echo "[local]\nlocalhost ansible_connection=local" > /etc/ansible/hosts

CMD ["/usr/sbin/init"]
