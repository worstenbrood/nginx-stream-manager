#!/bin/sh
# Run inside an official OpenWrt SDK container, with /src read-only and /out writable.
set -eu
[ -f /.dockerenv ] && [ "$(pwd)" = /builder ] || {
    echo 'Run inside the disposable OpenWrt SDK container in /builder.' >&2; exit 1;
}
[ -d /src/packages ] && [ -d /out ] || exit 1
[ -d scripts ] || ./setup.sh
if [ "${SDK_REUSE_FEEDS:-0}" = 1 ]; then
    if [ ! -f feeds/base.index ] || [ ! -f feeds/packages.index ] || [ ! -f feeds/luci.index ]; then
        ./scripts/feeds update -a -i
    fi
else
    ./scripts/feeds update -a
fi
# Only install source recipes needed by these two packages and their dependencies.
./scripts/feeds install nginx nginx-mod-stream python3 ip-full nftables-json firewall4 kmod-nft-socket rpcd luci-base
for name in nginx-stream-manager luci-app-nginx-stream-manager; do
    if [ -e "package/$name" ]; then
        [ "${SDK_REUSE_FEEDS:-0}" = 1 ] && [ ! -L "package/$name" ] || {
            echo "Package path already exists: $name" >&2; exit 1;
        }
        rm -rf "/builder/package/$name"
    fi
    cp -R "/src/packages/$name" "package/$name"
done
# SDK Config-build.in defaults many prebuilt packages to m independently of ALL.
# Start them disabled; Kconfig then selects only this application's dependencies.
awk '$1 == "config" && $2 ~ /^PACKAGE_/ { print "# CONFIG_" $2 " is not set" }' Config-build.in > .config
cat >> .config <<'EOF'
CONFIG_ALL=n
CONFIG_ALL_NONSHARED=n
CONFIG_ALL_KMODS=n
CONFIG_PACKAGE_nginx-stream-manager=m
CONFIG_PACKAGE_luci-app-nginx-stream-manager=m
CONFIG_SIGNED_PACKAGES=n
CONFIG_LUCI_JSMIN=n
CONFIG_LUCI_CSSTIDY=n
CONFIG_LUCI_SRCDIET=n
EOF
make defconfig
# Both packages contain scripts/assets only. Use SDK package targets directly;
# runtime dependencies are installed from official feeds in the userland test.
# This avoids rebuilding Python, Nginx and the kernel merely to package scripts.
for name in nginx-stream-manager luci-app-nginx-stream-manager; do
    make -C "package/$name" TOPDIR=/builder clean V=s
    make -C "package/$name" TOPDIR=/builder compile V=s
done
find bin -type f \( -name 'nginx-stream-manager_*.ipk' -o -name 'luci-app-nginx-stream-manager_*.ipk' \
    -o -name 'nginx-stream-manager-*.apk' -o -name 'luci-app-nginx-stream-manager-*.apk' \) \
    -exec cp '{}' /out/ \;
cp .config /out/sdk.config
cp feeds.conf.default /out/feeds.conf.default
{
    printf 'target=%s\nversion_path=%s\n' "${TARGET:-unknown}" "${VERSION_PATH:-unknown}"
    for feed in feeds/*; do
        [ -d "$feed/.git" ] || continue
        printf '%s=' "$(basename "$feed")"
        git -C "$feed" rev-parse HEAD
    done
} > /out/build-info.txt
python3 /src/tests/check_packages.py /out
(cd /out && find . -maxdepth 1 -type f \( -name '*.ipk' -o -name '*.apk' \) -exec sha256sum '{}' + > SHA256SUMS)
