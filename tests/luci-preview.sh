#!/bin/sh
# Disposable OpenWrt container only. Publish port 80 on host LOOPBACK only.
# The fixed credentials are exclusively for this throwaway UI fixture.
set -eu
[ -f /.dockerenv ] && [ -d /src/packages ] || exit 1
apk add uhttpd uhttpd-mod-ubus
mkdir -p /var/run /var/lock
/sbin/ubusd &
/sbin/procd -S &
for attempt in 1 2 3 4 5; do
    ubus list service >/dev/null 2>&1 && break
    sleep 1
done
/etc/init.d/firewall start
sh /src/install.sh
# Container-only listener; Docker must publish it on 127.0.0.1, never all host IPs.
nft insert rule inet fw4 input tcp dport 80 accept
cp /src/examples/generic.uci /etc/config/nginx_stream_manager
uci set nginx_stream_manager.main.enabled=0
uci commit nginx_stream_manager
printf 'nsm-local-preview\nnsm-local-preview\n' | passwd root
exec /usr/sbin/uhttpd -f -h /www -p 0.0.0.0:80 -x /cgi-bin -u /ubus
