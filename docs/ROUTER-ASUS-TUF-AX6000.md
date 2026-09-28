# Target device: ASUS TUF-AX6000

Inventory supplied by the user on 28 September 2026:

| Item | Value |
| --- | --- |
| Board | `asus,tuf-ax6000` |
| OpenWrt | `25.12.5 r33051-f5dae5ece4` |
| Target | `mediatek/filogic` |
| Kernel | `6.12.94` |
| Package architecture | `aarch64_cortex-a53`, inferred from Nginx build options and confirmed by the official firmware feeds |
| Nginx | `1.26.3`, dynamically built stream module |
| OpenSSL on the router | `3.5.7` |

The text `x86_64-pc-linux-gnu` in `nginx -V` describes the build environment. `--target=aarch64-openwrt-linux`, `--crossbuild=Linux::aarch64` and the compiler path confirm that the router binaries were built for ARM64.

## Local acceptance baseline

`tests/Dockerfile.openwrt25` downloads the official sysupgrade tarball, verifies SHA-256 and extracts only the squashfs filesystem. The firmware is not flashed or booted. The hash is `57b9a85c9059f2c35dc23a45324e14d724068f92f929853a82b13b91747b691f`.

The container runs real OpenWrt processes on the WSL2 host kernel. The feeds used supply Nginx `1.26.3-r4`, Python `3.13.9-r3` and OpenSSL `3.5.8-r2`. This matches the OpenWrt release and architecture but is not an exact copy of all installed router packages. A package manifest is saved when `/out` is mounted.

Manual installation, UCI/rpcd/procd, TCP/UDP over IPv4/IPv6, continued forwarding after a rejected reload, firewall4 reload, stop and uninstall passed locally. Procd reports missing cgroup permissions in Docker; cgroup isolation and reboot have not been validated.

Both `0.2.1-r1` APKs were also built with the official `mediatek-filogic-25.12.5` SDK, inspected as `noarch`, installed through `apk` in this container and tested. Removing an active manager preserves the modified configuration exactly. The SDK image had digest `sha256:011c367ac3e63ab3fd345bdb242564fedda033b40e73f883bd550fcf0d27bf9c`. The packages are unsigned; the local container explicitly accepts the project's own APK files only for this test.

## First test on the device

The router was not modified during development. For the first installation, use an available high port and a controlled backend with source-IP preservation disabled. Leave existing ports 53 and 51900 with the current setup. Check LuCI Save & Apply, start/stop and TCP/UDP request/reply. Then test IPv6 and firewall reload before transparent return routing, following [TESTING.md](TESTING.md).

[Official firmware, SDK and checksums](https://downloads.openwrt.org/releases/25.12.5/targets/mediatek/filogic/).
