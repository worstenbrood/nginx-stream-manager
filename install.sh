#!/bin/sh
# Install this source tree on an OpenWrt target. Does not start forwarding.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root on the OpenWrt router.' >&2; exit 1; }
[ -f /etc/openwrt_release ] || { echo 'This installer is for OpenWrt only.' >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$base/packages/nginx-stream-manager/files/nsm.py" ] || exit 1
if { command -v apk >/dev/null 2>&1 && apk info -e nginx-stream-manager >/dev/null 2>&1; } ||
   { command -v opkg >/dev/null 2>&1 && opkg status nginx-stream-manager 2>/dev/null | grep -q '^Status: .* installed'; }; then
    echo 'Package-managed installation found; upgrade through the package manager.' >&2
    exit 1
fi
if [ -x /etc/init.d/nginx-stream-manager ]; then
    echo 'Existing installation found. Stop the manager before upgrading.' >&2
    if [ -f /var/run/nginx-stream-manager/nginx.pid ] && kill -0 "$(cat /var/run/nginx-stream-manager/nginx.pid)" 2>/dev/null; then
        exit 1
    fi
    /usr/libexec/nginx-stream-manager cleanup
fi
packages='luci-base nginx-mod-stream python3 ip-full nftables-json firewall4 kmod-nft-socket'
# Keep an existing nginx provider instead of forcing a conflicting variant.
command -v nginx >/dev/null 2>&1 || packages="$packages nginx-full"
if command -v apk >/dev/null 2>&1; then
    apk add $packages
elif command -v opkg >/dev/null 2>&1; then
    opkg update
    opkg install $packages
else
    echo 'No supported package manager found.' >&2; exit 1
fi
# Kernel packages must come from this router's matching release feeds.
python3 -c 'import fcntl, ipaddress, json, pathlib, subprocess'
mkdir -p /usr/libexec/rpcd /usr/share/nftables.d/chain-pre/input /etc/hotplug.d/iface
mkdir -p /www/luci-static/resources/view/nsm /usr/share/luci/menu.d /usr/share/rpcd/acl.d
src="$base/packages/nginx-stream-manager/files"
cp "$src/nsm_importer.py" /usr/libexec/nsm_importer.py
cp "$src/nsm.py" /usr/libexec/nginx-stream-manager
cp "$src/maintenance" /usr/libexec/nginx-stream-manager-maintenance
cp "$src/rpc" /usr/libexec/rpcd/luci.nginx-stream-manager
cp "$src/nginx-stream-manager.init" /etc/init.d/nginx-stream-manager
cp "$src/hotplug" /etc/hotplug.d/iface/95-nginx-stream-manager
cp "$src/return.nft" /usr/share/nftables.d/chain-pre/input/90-nginx-stream-manager.nft
chmod 755 /usr/libexec/nginx-stream-manager /usr/libexec/nginx-stream-manager-maintenance /usr/libexec/rpcd/luci.nginx-stream-manager /etc/init.d/nginx-stream-manager
if [ ! -f /etc/config/nginx_stream_manager ]; then
    cp "$src/nginx_stream_manager" /etc/config/nginx_stream_manager
    chmod 600 /etc/config/nginx_stream_manager
fi
ui="$base/packages/luci-app-nginx-stream-manager"
cp "$ui"/htdocs/luci-static/resources/view/nsm/*.js /www/luci-static/resources/view/nsm/
cp "$ui/root/usr/share/luci/menu.d/luci-app-nginx-stream-manager.json" /usr/share/luci/menu.d/
cp "$ui/root/usr/share/rpcd/acl.d/luci-app-nginx-stream-manager.json" /usr/share/rpcd/acl.d/
fw4 check
/etc/init.d/firewall reload
/etc/init.d/rpcd restart
rm -f /tmp/luci-indexcache
rm -rf /tmp/luci-modulecache
printf '%s\n' 'Installed. Log out of LuCI and back in: Services → Stream Manager.' 'Manager remains stopped. Configure and validate before starting.'
