ARG VERSION=latest
FROM rockylinux/rockylinux:$VERSION
LABEL org.opencontainers.image.source=https://github.com/kyl191/ansible-role-openvpn
LABEL org.opencontainers.image.title="Rocky Linux $VERSION with Ansible"
LABEL org.opencontainers.image.description="Rocky Linux with Ansible, based off https://github.com/diodonfrost/docker-ansible/blob/master/rockylinux-ansible/Dockerfile.rockylinux-10"

ENV container docker

# Update
RUN dnf -y update && dnf -y install dnf-plugins-core && dnf clean all

# Install systemd
RUN dnf -y install systemd && dnf clean all; \
(cd /lib/systemd/system/sysinit.target.wants/; for i in *; do [ $i == systemd-tmpfiles-setup.service ] || rm -f $i; done); \
rm -f /lib/systemd/system/multi-user.target.wants/*; \
rm -f /etc/systemd/system/*.wants/*; \
rm -f /lib/systemd/system/local-fs.target.wants/*; \
rm -f /lib/systemd/system/sockets.target.wants/*udev*; \
rm -f /lib/systemd/system/sockets.target.wants/*initctl*; \
rm -f /lib/systemd/system/basic.target.wants/*; \
rm -f /lib/systemd/system/anaconda.target.wants/*;

# Install Ansible
RUN dnf -y install \
           git \
           ansible-core \
           python3 \
           python3-pip \
           sudo \
           openssh-server \
           openssh-clients \
           unzip \
           rsync \
           which \
           fuse-libs \
           && dnf clean all

# Install packages for ansible-role-openvpn
# python3-dnf required for ansible.builtin.package_facts
RUN dnf -y install \
    firewalld python3-firewall procps-ng \
    iproute \
    python3-dnf \
    && dnf clean all

RUN sed -i -e 's/^\(Defaults\s*requiretty\)/#--- \1/'  /etc/sudoers

RUN echo -e '[local]\nlocalhost ansible_connection=local' > /etc/ansible/hosts

VOLUME ["/sys/fs/cgroup"]

CMD ["/usr/sbin/init"]
