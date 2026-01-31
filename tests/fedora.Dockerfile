ARG VERSION=latest
FROM registry.fedoraproject.org/fedora:$VERSION
LABEL org.opencontainers.image.source=https://github.com/kyl191/ansible-role-openvpn
LABEL org.opencontainers.image.title="Fedora $VERSION with Ansible"
LABEL org.opencontainers.image.description="Fedora with Ansible, based off https://github.com/diodonfrost/docker-ansible/blob/master/fedora-ansible/Dockerfile.fedora-40"

# Update Fedora
RUN dnf -y update && dnf clean all

# Install systemd
RUN dnf -y install systemd && dnf clean all; \
(cd /lib/systemd/system/sysinit.target.wants/; for i in *; do [ $i == systemd-tmpfiles-setup.service ] || rm -vf $i; done); \
rm -vf /lib/systemd/system/multi-user.target.wants/*; \
rm -vf /etc/systemd/system/*.wants/*; \
rm -vf /lib/systemd/system/local-fs.target.wants/*; \
rm -vf /lib/systemd/system/sockets.target.wants/*udev*; \
rm -vf /lib/systemd/system/sockets.target.wants/*initctl*; \
rm -vf /lib/systemd/system/basic.target.wants/*; \
rm -vf /lib/systemd/system/anaconda.target.wants/*;

# Install Ansible
RUN dnf -y install \
           git \
           ansible \
           sudo \
           which \
           openssh-server openssh-clients \
           findutils \
           unzip \
           rsync \
           libxcrypt-compat \
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
