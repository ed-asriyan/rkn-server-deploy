# vnstat

Installs and enables [vnstat](https://humdi.net/vnstat/), a network traffic monitor.

## What it does

- Installs the `vnstat` package via the system package manager
- Enables and starts the `vnstat` systemd service

## Usage

Add the role to your playbook:

```yaml
roles:
  - role: vnstat
```
