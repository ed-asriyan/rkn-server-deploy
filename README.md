# RKN proxy server deploy [![CI | pre-commit](https://github.com/ed-asriyan/rkn-server-deploy/actions/workflows/CI-pre-commit.yml/badge.svg)](https://github.com/ed-asriyan/rkn-server-deploy/actions/workflows/CI-pre-commit.yml)

Ansible-based deployment for a Debian server running [Xray](https://xtls.github.io/en/) with VLESS + REALITY.

The repository is public so the deployment logic can be versioned and reused, but the actual deployment should be triggered from a separate private GitHub repository. That keeps workflow logs, server details, and generated client URIs private.

## What this repository does
- Connects to a target Debian server over SSH as `root`
- Installs and configures Xray
- Generates VLESS client URIs into `uris.txt`
- Uploads `uris.txt` as a GitHub Actions artifact in the caller repository

## Tested client
- [Hiddify](https://hiddify.com/#app)

## Deploy your own server
To deploy your own server, call the reusable workflow defined in [.github/workflows/deploy.yml](./.github/workflows/deploy.yml) via `workflow_call` from your private GitHub repository.

Do not trigger deployments from this public repository. The workflow produces sensitive output, including:
- server IP or domain
- SNI values
- generated VLESS URIs for end users

Use a **private repository** as the caller so logs and artifacts are visible only to you.

### Requirements
- A VPS running Debian 12 or Debian 13
- SSH access as `root`
- A GitHub private repository to trigger deployments
- An SSH private key stored as a GitHub Actions secret in that private repository

If your VPS provider supports injecting an SSH public key during server creation, use that. Otherwise, add your public key to `/root/.ssh/authorized_keys` manually.

### Create a private caller repository
1. Create a new private GitHub repository.
2. Add this secret to the private repository:

| Secret | Description |
| --- | --- |
| `SSH_PRIVATE_KEY` | Private key used by the workflow to connect to the target server over SSH |

3. Create a workflow such as `.github/workflows/deploy-myserver.yml` in the private repository:

```yaml
name: Deploy my server

on:
  workflow_dispatch:

jobs:
  deploy:
    uses: ed-asriyan/rkn-server-deploy/.github/workflows/deploy.yml@master
    with:
      name: my-server
      host: 1.2.3.4
      port: 443
      fingerprint: chrome
      fallback_proxy_target: example.com:443
      snis: example.com,www.example.com
      number_of_users: 256
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
```

4. Run that workflow from the Actions tab of the private repository.
5. Download the generated `uris.txt` artifact from the workflow run.

### Workflow inputs
The reusable workflow accepts these inputs:
| Input | Description |
| --- | --- |
| `name` | Label added to generated client URIs |
| `host` | Public IP address or DNS name clients will connect to |
| `port` | Port exposed by Xray on the target host |
| `fingerprint` | REALITY client fingerprint value |
| `fallback_proxy_target` | Upstream host:port for non-VLESS traffic |
| `snis` | Comma-separated SNI values used by REALITY clients (e.g. `example.com,www.example.com`) |
| `number_of_users` | Number of client URIs to generate |

### What the workflow does
1. Checks out this repository.
2. Starts an SSH agent with the private key from the caller repository.
3. Runs [proxies.yml](./proxies.yml) against the target host.
4. Writes generated client URIs to `uris.txt`.
5. Uploads `uris.txt` as a workflow artifact.

## Development
This section is only for working on this repository itself.
- Install [pre-commit](https://pre-commit.com/#install).
- Run `pre-commit install`.
- Make sure your local SSH key can access a test server before running Ansible manually.

The playbook must work for Debian.
