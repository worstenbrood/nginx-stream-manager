#!/bin/sh
# Only for the disposable OpenWrt test container, never the real router.
set -eu
[ -f /.dockerenv ] && [ -d /src/packages ] || exit 1
mkdir -p /var/run /var/lock
/sbin/ubusd &
ubus_pid=$!
/sbin/procd -S &
procd_pid=$!
trap 'kill "$procd_pid" "$ubus_pid" 2>/dev/null || true' EXIT
for attempt in 1 2 3 4 5; do
    ubus list service >/dev/null 2>&1 && break
    sleep 1
done
# This firewall belongs only to this disposable container.
/etc/init.d/firewall start
case "${NSM_INSTALL_MODE:-manual}" in
    manual) sh /src/install.sh ;;
    package)
        [ -d /packages ] || exit 1
        if command -v apk >/dev/null 2>&1; then
            apk add --allow-untrusted /packages/nginx-stream-manager-*.apk /packages/luci-app-nginx-stream-manager-*.apk
        else
            opkg install /packages/nginx-stream-manager_*.ipk /packages/luci-app-nginx-stream-manager_*.ipk
        fi
        ;;
    *) echo 'Unknown NSM_INSTALL_MODE' >&2; exit 1 ;;
esac
if [ -d /out ]; then
    {
        cat /etc/openwrt_release
        uname -r
        nginx -V 2>&1
        if command -v apk >/dev/null 2>&1; then apk info -v; else opkg list-installed; fi
    } > "/out/userland-${NSM_INSTALL_MODE:-manual}-info.txt"
fi
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    ubus list luci.nginx-stream-manager >/dev/null 2>&1 && break
    sleep 1
done
ubus -v list luci.nginx-stream-manager
python3 /src/tests/openwrt-userland.py
# Exercise uninstall hooks with a running instance as well as the earlier explicit stop.
uci set nginx_stream_manager.testbackend.port=19061
uci commit nginx_stream_manager
/etc/init.d/nginx-stream-manager start
cp /etc/config/nginx_stream_manager /tmp/nsm-config-before-remove
case "${NSM_INSTALL_MODE:-manual}" in
    manual) sh /src/uninstall.sh ;;
    package)
        if command -v apk >/dev/null 2>&1; then
            apk del luci-app-nginx-stream-manager nginx-stream-manager
        else
            opkg remove luci-app-nginx-stream-manager nginx-stream-manager
        fi
        ;;
esac
[ ! -e /usr/libexec/nginx-stream-manager ]
[ ! -e /var/run/nginx-stream-manager/state.json ]
rules="$(nft list chain inet fw4 input)"
case "$rules" in *nsm-listener-testservice*|*nsm-listener-renamed_service*) echo 'Listener permit remained after uninstall' >&2; exit 1 ;; esac
preserved=0
for config in /etc/config/nginx_stream_manager /etc/config/nginx_stream_manager.opkg-save /etc/config/nginx_stream_manager.apk-save; do
    if [ -f "$config" ]; then cmp /tmp/nsm-config-before-remove "$config"; preserved=1; break; fi
done
[ "$preserved" = 1 ]
echo 'OpenWrt userland install/RPC/service/uninstall checks passed.'
