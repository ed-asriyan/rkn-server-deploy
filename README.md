# RKN proxy server deploy [![CI | pre-commit](https://github.com/ed-asriyan/rkn-server-deploy/actions/workflows/CI-pre-commit.yml/badge.svg)](https://github.com/ed-asriyan/rkn-server-deploy/actions/workflows/CI-pre-commit.yml)

Ansible-based deployment for Debian servers running [Xray](https://xtls.github.io/en/) with VLESS + REALITY.

The repository is public so the deployment logic can be versioned and reused, but the actual deployment should be triggered from a separate private GitHub repository. That keeps workflow logs, server details, and generated client URIs private.

## What this repository does
- Connects to one or more target Debian servers over SSH as `root`
- Installs and configures Xray on each server
- Optionally chains servers in relay pairs (censored-country server → free-country server)
- Generates VLESS client URIs into `uris.txt`
- Uploads `uris.txt` as a GitHub Actions artifact in the caller repository

## Tested client
- [Hiddify](https://hiddify.com/#app)

## Deploy your own server
To deploy your own server(s), call the reusable workflow defined in [.github/workflows/deploy.yml](./.github/workflows/deploy.yml) via `workflow_call` from your private GitHub repository.

Do not trigger deployments from this public repository. The workflow produces sensitive output, including:
- server IPs or domains
- SNI values
- generated VLESS URIs for end users

Use a **private repository** as the caller so logs and artifacts are visible only to you.

### Requirements
- One or more VPS instances running Debian 12 or Debian 13
- SSH access as `root` on each server
- A GitHub private repository to trigger deployments
- An SSH private key stored as a GitHub Actions secret in that private repository

If your VPS provider supports injecting an SSH public key during server creation, use that. Otherwise, add your public key to `/root/.ssh/authorized_keys` manually.

### Create a private caller repository
1. Create a new private GitHub repository.
2. Add this secret to the private repository:

| Secret | Description |
| --- | --- |
| `SSH_PRIVATE_KEY` | Private key used by the workflow to connect to the target servers over SSH |

3. Create a workflow such as `.github/workflows/deploy.yml` in the private repository. Examples below.

#### Minimal example — single server, connects directly to the internet

```yaml
name: Deploy

on:
  workflow_dispatch:

jobs:
  deploy:
    uses: ed-asriyan/rkn-server-deploy/.github/workflows/deploy.yml@master
    with:
      servers: |
        {
          "myserver": {
            "host": "1.2.3.4",
            "port": 443,
            "fingerprint": "chrome",
            "fallback_proxy_target": "example.com:443",
            "snis": "example.com,www.example.com",
            "number_of_users": 256
          }
        }
      whitelist-domains: "example.ru,test.ru"
      seed: 42
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
```

#### Relay example — censored server forwards traffic through a free server

```yaml
name: Deploy

on:
  workflow_dispatch:

jobs:
  deploy:
    uses: ed-asriyan/rkn-server-deploy/.github/workflows/deploy.yml@master
    with:
      servers: |
        {
          "ru": {
            "host": "1.2.3.4",
            "port": 443,
            "fingerprint": "chrome",
            "fallback_proxy_target": "example.com:443",
            "snis": "example.com",
            "number_of_users": 256
          },
          "nl": {
            "host": "5.6.7.8",
            "port": 443,
            "fingerprint": "chrome",
            "fallback_proxy_target": "example.com:443",
            "snis": "example.com",
            "number_of_users": 256
          }
        }
      whitelist-domains: "example.ru,test.ru"
      pairs: '{"ru": "nl"}'
      seed: 42
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
```

Clients connect to `ru`, which forwards traffic to `nl`, which exits to the internet. `nl` has no pair so it connects directly. Both servers are deployed and configured automatically. `uris.txt` contains URIs for `ru` only (the entry point).

4. Run that workflow from the Actions tab of the private repository.
5. Download the generated `uris.txt` artifact from the workflow run.

### Workflow inputs

| Input | Required | Description |
| --- | --- | --- |
| `servers` | yes | JSON object of server configurations. Each key is a server name; each value is an object with: `host`, `port`, `fingerprint`, `fallback_proxy_target`, `snis` (comma-separated), `number_of_users` |
| `whitelist-domains` | yes | Comma-separated list of RU domains to whitelist (e.g. `"example.ru,test.ru"`) |
| `pairs` | yes | JSON object of relay pairs. Key proxies to value (e.g. `{"ru":"nl"}`). Servers not listed connect directly to the internet. |
| `seed` | yes | Integer seed for deterministic UUID and keypair generation. When set, re-running with the same seed and server hosts produces identical client configs. Omit to generate randomly each run |

### What the workflow does
1. Checks out this repository.
2. Starts an SSH agent with the private key from the caller repository.
3. Generates a keypair and UUIDs for each server (deterministically if `seed` is set).
4. Runs [proxies.yml](./proxies.yml) against all target servers in parallel.
5. Writes generated client URIs to `uris.txt`.
6. Uploads `uris.txt` as a workflow artifact.

## Development
This section is only for working on this repository itself.
- Install [pre-commit](https://pre-commit.com/#install).
- Run `pre-commit install`.
- Make sure your local SSH key can access a test server before running Ansible manually.

The playbook must work for Debian.
