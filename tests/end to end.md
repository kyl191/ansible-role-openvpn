# End to End Testing notes

Loose idea is will need to start a bunch of VMs, 1 for each distro under test.

Separate client will:

1. take the config produced by the Ansible run
2. start the OpenVPN client using the config file
3. curl `icanhazip.com`

Timeout/failure indicates that the OpenVPN connection didn't work for some reason. The IP address returned by #3 should be the IP address of the server, not the test client.

Test client could fetch `icanhazip.com` again to make sure the IP address changes as well.

## Implementation

Need to spin up different distros, so probably AWS or GCP (or Azure).

AWS: [t4g.small instances are free for 750 hours till Dec 31 2025](https://aws.amazon.com/ec2/instance-types/t4/)

Potential for the test client to be an existing VPS? Would want to test different OpenVPN versions though?

Spinning up is ~easy - Terraform can handle setting it up (and VPC and tagging).

Having one system run the ansible playbook is also ~straightforward.

But should the system running the ansible playbook be the system testing the client connections? Potentially, but then there could be issues recovering from network misconfigurations (admittedly probably not).

Having a separate script that SSHes to a test node would be better... except SSH outgoing packets might get caught and forced to go via the VPN tunnel?

## Github Runner not suitable

We can probably mess with the network settings directly on the machine [since sudo is usable](https://docs.github.com/en/actions/using-github-hosted-runners/using-github-hosted-runners/about-github-hosted-runners#administrative-privileges), but that doesn't help with triggering the client test.

Maybe Github Actions could be the test runner that triggers the AWS build then tests locally?
