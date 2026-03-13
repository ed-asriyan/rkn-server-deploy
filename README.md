# Proxy [![CI | pre-commit](https://github.com/ed-asriyan/xray-server/actions/workflows/CI-pre-commit.yml/badge.svg)](https://github.com/ed-asriyan/xray-server/actions/workflows/CI-pre-commit.yml) [![CD | Deploy server](https://github.com/ed-asriyan/xray-server/actions/workflows/CD-production-task.yml/badge.svg)](https://github.com/ed-asriyan/xray-server/actions/workflows/CD-production-task.yml)
This is deployment for my personal server with [xray](https://xtls.github.io/en/) on board for me and my friends to bypass internet censorship.

## Vless clients that work with this setup
https://hiddify.com#app
There are 2 components: **[Supabase](https://supabase.com) instance** and **proxy hosts**. GH Actions are configured to deploy proxy hosts on demand.

# [Supabase](https://supabase.com)
Stores the list of VPN providers and their user URIs. After each deployment the CD workflow upserts the provider record and bulk-inserts all generated URIs, then invokes the `shuffle_vpn` edge function to redistribute them.

Required secrets:
* `SUPABASE_URL`: Supabase project URL (e.g. `https://xxxx.supabase.co`)
* `SUPABASE_SERVICE_ROLE_KEY`: Supabase service role API key

## Proxy
As many proxy hosts as needed can be deployed; each one must have its own public IP address and/or DNS record.
Each proxy host is a Debian linux host with [xray-core](https://github.com/xtls/xray-core) installed (role: `xray`):
* listens on the configured port using VLESS + REALITY
* forwards non-VLESS traffic to `fallback_proxy_target`

Playbook: [proxies.yml](./proxies.yml)

### Deploying a proxy
Trigger the **CD | Deploy server** workflow manually from the GitHub Actions UI and fill in the inputs:

| Input | Description |
|---|---|
| `name` | Public server name (used as the provider name in Supabase) |
| `host` | Public IP or domain of the target server |
| `port` | Port xray listens on (default: `443`) |
| `fingerprint` | REALITY fingerprint (`chrome`, `safari`, or `ios`) |
| `fallback_proxy_target` | Fallback proxy target for non-VLESS traffic |
| `sni` | SNI value for REALITY |
| `number_of_users` | Number of user configs to generate (default: `1024`) |

The workflow will:
1. Run the Ansible playbook to install / update xray on the target host
2. Upload the generated `uris.txt` as a build artifact
3. Upsert the provider and its URIs in Supabase
4. Invoke the `shuffle_vpn` edge function

# Development
This part requires [Ansible](https://www.ansible.com) knowledge. The deployment is tested on and implemented for Debian only.

## At the very beginning
1. Initialize pre-commit hook to prevent secrets from being leaked:
   1. Install [pre-commit](https://pre-commit.com/#install)
   2. Run: `pre-commit install`
2. Add the SSH private key to `id_rsa` in the root of the repository. **Make sure only you can read/write it: `chmod 600 id_rsa`**

## Required GitHub secrets
* `KNOWN_HOSTS`: contents of `.ssh/known_hosts` for your servers
* `SSH_PRIVATE_KEY`: SSH private key to access the servers
* `SUPABASE_URL`: Supabase project URL
* `SUPABASE_SERVICE_ROLE_KEY`: Supabase service role API key
