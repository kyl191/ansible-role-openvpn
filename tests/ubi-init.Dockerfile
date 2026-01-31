ARG VERSION=9
FROM registry.access.redhat.com/ubi${VERSION}-init:latest
LABEL org.opencontainers.image.source=https://github.com/kyl191/ansible-role-openvpn
LABEL org.opencontainers.image.title="RHEL UBI ${VERSION}-init with Ansible"


RUN dnf -y install \
           git \
           python3-pip \
           sudo \
           unzip \
           rsync \
           which \
           && dnf clean all

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install ansible

RUN sed -i -e 's/^\(Defaults\s*requiretty\)/#--- \1/'  /etc/sudoers

RUN mkdir /etc/ansible && \
    echo -e '[local]\nlocalhost ansible_connection=local' > /etc/ansible/hosts

VOLUME ["/sys/fs/cgroup"]

CMD ["/usr/sbin/init"]
