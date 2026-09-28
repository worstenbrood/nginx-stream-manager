#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] && [ -f /etc/openwrt_release ] || exit 1
if { command -v apk >/dev/null 2>&1 && apk info -e nginx-stream-manager >/dev/null 2>&1; } ||
   { command -v opkg >/dev/null 2>&1 && opkg status nginx-stream-manager 2>/dev/null | grep -q '^Status: .* installed'; }; then
    echo 'Remove package-managed installations with the package manager.' >&2
    exit 1
fi
if [ -x /etc/init.d/nginx-stream-manager ]; then
    /etc/init.d/nginx-stream-manager stop
    /usr/libexec/nginx-stream-manager cleanup
    /etc/init.d/nginx-stream-manager disable
fi
rm -f /usr/libexec/nginx-stream-manager /usr/libexec/nginx-stream-manager-maintenance /usr/libexec/nsm_importer.py
rm -f /usr/libexec/rpcd/luci.nginx-stream-manager /etc/init.d/nginx-stream-manager
rm -f /etc/hotplug.d/iface/95-nginx-stream-manager
rm -f /usr/share/nftables.d/chain-pre/input/90-nginx-stream-manager.nft
rm -f /usr/share/luci/menu.d/luci-app-nginx-stream-manager.json
rm -f /usr/share/rpcd/acl.d/luci-app-nginx-stream-manager.json
rm -f /www/luci-static/resources/view/nsm/config.js /www/luci-static/resources/view/nsm/overview.js /www/luci-static/resources/view/nsm/logs.js /www/luci-static/resources/view/nsm/import.js
/etc/init.d/firewall reload
/etc/init.d/rpcd restart
rm -f /tmp/luci-indexcache
rm -rf /tmp/luci-modulecache
printf '%s\n' 'Removed manual installation. UCI configuration, logs and dependencies retained.'
