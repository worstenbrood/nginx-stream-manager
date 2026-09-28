# Reproducible builds and OpenWrt checks

All actions below run locally. They install nothing on the real router. The target device runs OpenWrt 25.12.5 on `mediatek/filogic`; see the [router inventory](ROUTER-ASUS-TUF-AX6000.md). OpenWrt 24.10.5 remains a separate IPK reference.

## Read-only router inventory

```sh
sh tests/router-info.sh
```

The script reads the board/release, kernel version, Nginx build options, package architecture and nftables version. It does not export UCI configuration or passwords or perform service actions. If the project directory is not yet on the router, start with `ubus call system board`, `uname -r` and `nginx -V 2>&1`.

## Building packages in the official SDK

On an x86-64 host, from the project directory:

```sh
mkdir -p artifacts/openwrt-24.10.5-x86-64
docker run --rm --platform linux/amd64 \
  -e TARGET=x86/64 -e VERSION_PATH=releases/24.10.5 \
  -v "$PWD:/src:ro" \
  -v "$PWD/artifacts/openwrt-24.10.5-x86-64:/out" \
  ghcr.io/openwrt/sdk:x86-64-24.10.5 \
  sh /src/tests/sdk-build.sh
```

The script installs the required feed sources in the disposable container, copies both packages into the SDK, builds them and inspects their contents. The source directory is read-only. The output directory receives packages, `sdk.config`, feed information, `build-info.txt` and `SHA256SUMS`. Checks include `Architecture: all` (IPK) or `noarch` (APK), dependencies, executable bits, a disabled default configuration, conffiles, the RPC wrapper and LuCI files. Hooks are checked for shell syntax but are not executed.

Because both packages contain only scripts and assets, the build uses the SDK package targets directly. LuCI minification is disabled. Python, Nginx and kernel modules are not recompiled; their dependency metadata remains in the packages, and installation testing uses the official runtime feeds. This is not a full cross-build of all dependencies.

A missing or invalid package causes the command to fail. APK inspection uses `apk adbdump --format json` and `apk extract` from the SDK; optionally, `APK_TOOL` can point to another compatible apk executable. An SDK build is not an installation or router test. Packages are unsigned and are not published automatically.

For the ASUS target release, replace the SDK tag with `mediatek-filogic-25.12.5`, `TARGET` with `mediatek/filogic`, `VERSION_PATH` with `releases/25.12.5` and the output directory with `artifacts/openwrt-25.12.5-mediatek-filogic`. Do not mix artifacts from different releases in one output directory.

### ARM64 host without x86 binfmt

The development computer used does not have working Docker x86 emulation. The additional image uses PRoot and QEMU in userspace, entirely inside the container:

```sh
docker build -t nsm-sdk-emulated:24.10.5 -f tests/Dockerfile.sdk-arm64 .
docker run --rm --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  -e TARGET=x86/64 -e VERSION_PATH=releases/24.10.5 \
  -v "$PWD:/src:ro" \
  -v "$PWD/artifacts/openwrt-24.10.5-x86-64:/out" \
  nsm-sdk-emulated:24.10.5
```

This does not register a host binfmt handler. SYS_PTRACE is used only for PRoot inside the container. Expect a slower build. Select the SDK image with `--build-arg SDK_IMAGE=...`; Alpine and feed revisions can change, so retain the image digest and build log with shared artifacts.

When resuming from a saved container with fully fetched feeds, add `-e SDK_REUSE_FEEDS=1`. This uses existing indexes or rebuilds missing ones without running `git pull` on the pinned release tag. Only the two project package source directories under `/builder/package` are replaced with the current source copy. Do not use this with missing or partially fetched feeds.

For the ASUS target build:

```sh
mkdir -p artifacts/openwrt-25.12.5-mediatek-filogic
docker build --build-arg SDK_IMAGE=ghcr.io/openwrt/sdk:mediatek-filogic-25.12.5 \
  -t nsm-sdk-emulated:25.12.5 -f tests/Dockerfile.sdk-arm64 .
docker run --rm --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  -e TARGET=mediatek/filogic -e VERSION_PATH=releases/25.12.5 \
  -v "$PWD:/src:ro" \
  -v "$PWD/artifacts/openwrt-25.12.5-mediatek-filogic:/out" \
  nsm-sdk-emulated:25.12.5
```

## Real OpenWrt userspace checks

This separate ARM64 image contains the official 24.10.5 rootfs, checked against the SHA-256 in the download index. Nginx, Python, LuCI and other dependencies come from the corresponding signed OpenWrt feeds.

```sh
docker build -t nsm-openwrt-userland:24.10.5 -f tests/Dockerfile.openwrt .
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  -v "$PWD:/src:ro" nsm-openwrt-userland:24.10.5
```

This runs the real installer, starts real ubusd/procd/rpcd processes and checks:

- actual RPC methods and signatures;
- start, reload and stop through the detached RPC action;
- TCP and UDP request/reply over IPv4 and IPv6, including after a rejected reload;
- firewall input permits after firewall4 reload and their removal after stop;
- rejection of an invalid committed configuration while preserving the previous runtime;
- refusal of a manual upgrade while Nginx is active;
- uninstall while preserving user configuration.

**The container uses the host kernel.** This does not validate OpenWrt kernel modules, reboot, physical interfaces, hardware offloading or LuCI browser operation. The OpenWrt rootfs contains package metadata for its own kernel, but that kernel is not running here. No host network, Docker socket or host module directory is mounted, and CAP_SYS_MODULE is not granted.

On an x86 host, this ARM64 rootfs requires emulation. For a native x86 rootfs, update both the download path and the verified SHA-256; do not substitute a tarball without checking its checksum.

### ASUS firmware baseline for OpenWrt 25.12.5

```sh
docker build -t nsm-openwrt-userland:25.12.5 -f tests/Dockerfile.openwrt25 .
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  -v "$PWD:/src:ro" \
  -v "$PWD/artifacts/openwrt-25.12.5-mediatek-filogic:/out" \
  nsm-openwrt-userland:25.12.5
```

This image only extracts the verified ASUS firmware. The same host-kernel and reboot limitations apply; procd reports missing cgroup permissions in Docker. The current package manifest is saved in `/out`.

After a successful package build, test the real package hooks by adding these arguments to `docker run`:

```sh
-e NSM_INSTALL_MODE=package \
-v "$PWD/artifacts/openwrt-25.12.5-mediatek-filogic:/packages:ro"
```

This disposable container explicitly accepts the project's own unsigned APK files and performs installation, runtime tests and removal. The 24.10.5 image uses the IPK output directory and `opkg` for the same mode.

## Sources

- [Official OpenWrt SDK container instructions](https://github.com/openwrt/docker)
- [24.10.5 x86/64 downloads](https://downloads.openwrt.org/releases/24.10.5/targets/x86/64/)
- [24.10.5 ARM64 rootfs and checksum](https://downloads.openwrt.org/releases/24.10.5/targets/armsr/armv8/)
- [25.12.5 mediatek/filogic firmware, SDK and checksums](https://downloads.openwrt.org/releases/25.12.5/targets/mediatek/filogic/)
