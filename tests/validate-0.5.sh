#!/bin/sh
# Run from a POSIX shell in this repository, with a working Docker daemon.
# Every runtime test uses a disposable container; no router is contacted.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
docker run --rm -v "$base:/src:ro" nsm-test:0.4.0 sh -c 'cd /src && python3 -m unittest discover -s tests && python3 tests/check_static.py'
docker run --rm --tmpfs /mnt/nsm-test:rw,size=16m,mode=700 -v "$base:/src:ro" nsm-test:0.4.0 python3 tests/integration_archive.py
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE -v "$base:/src:ro" nsm-test:0.4.0 python3 tests/integration_features.py
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE -e NSM_EXPECT_NO_STREAM_REALIP=1 -v "$base:/src:ro" nsm-openwrt-userland:25.12.5 python3 /src/tests/integration_features.py
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE -v "$base:/src:ro" nsm-openwrt-userland:25.12.5 sh /src/tests/openwrt-userland.sh
mkdir -p "$base/artifacts/0.5.0-openwrt-25.12.5"
docker run --rm --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  -e TARGET=mediatek/filogic -e VERSION_PATH=releases/25.12.5 -e SDK_REUSE_FEEDS=1 \
  -v "$base:/src:ro" -v "$base/artifacts/0.5.0-openwrt-25.12.5:/out" \
  nsm-sdk-ready:25.12.5 sh -c 'cp /src/tests/sdk-build.sh /tmp/nsm-sdk-build.sh; sh /tmp/nsm-sdk-build.sh > /out/build.log 2>&1; result=$?; tail -n 6 /out/build.log; exit $result'
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE -e NSM_INSTALL_MODE=package \
  -v "$base:/src:ro" -v "$base/artifacts/0.5.0-openwrt-25.12.5:/out" \
  -v "$base/artifacts/0.5.0-openwrt-25.12.5:/packages:ro" nsm-openwrt-userland:25.12.5 \
  sh -c 'sh /src/tests/openwrt-userland.sh > /out/package-userland.log 2>&1; result=$?; tail -n 12 /out/package-userland.log; exit $result'
