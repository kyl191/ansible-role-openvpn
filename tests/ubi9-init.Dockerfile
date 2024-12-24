FROM redhat/ubi9-init:latest

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
