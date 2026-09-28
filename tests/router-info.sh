#!/bin/sh
# Read-only inventory: no package install, configuration export or service action.
set -u
[ -f /etc/openwrt_release ] || { echo 'Run on OpenWrt.' >&2; exit 1; }
printf '\n=== OpenWrt board/release ===\n'
ubus call system board
printf '\n=== Running kernel ===\n'
uname -r
printf '\n=== Nginx version/build options ===\n'
nginx -V 2>&1
printf '\n=== Package architecture ===\n'
if command -v apk >/dev/null 2>&1; then
    apk --print-arch
elif command -v opkg >/dev/null 2>&1; then
    opkg print-architecture
else
    echo 'No apk/opkg found.'
fi
printf '\n=== nftables version ===\n'
nft --version 2>&1
