# yggdrasil
Installs and configures [Yggdrasil](https://yggdrasil-network.github.io), an end-to-end encrypted IPv6 network.

## What it does
1. Installs the `yggdrasil` package via apt
2. Generates `/etc/yggdrasil/yggdrasil.conf` using `yggdrasil -genconf` (only if not already present)
3. Enables and starts the `yggdrasil` systemd service
