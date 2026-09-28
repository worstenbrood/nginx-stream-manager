# Validation and test status

## Monitoring and management — 0.5.0

Date: 28 September 2026.

- 132 unit/regression tests passed, including real local TCP, TLS certificate-name and DNS probes, configuration preview/restore, hysteresis, snapshots of the applied configuration and archive checkpoints.
- Eleven Nginx stream feature tests on Alpine and eleven on OpenWrt 25.12.5/Nginx 1.26.3 passed, including exact/longest-wildcard/default SNI priority with real TLS connections. The OpenWrt real-IP test checks the expected rejection of the missing module.
- The extended manual installation test on OpenWrt 25.12.5 passed: real RPC/procd/firewall4, IPv4/IPv6 TCP/UDP, byte/timing measurements, metrics export, working snapshots, blocked restore while the manager is running, monitoring, diagnostics and restoration after stop.
- Archive storage tested on a real temporary tmpfs mount: checkpoint/restore without double counting, seven-day history and no fallback to flash when the mount is missing.
- Syntax checks passed for eleven shell scripts plus package hooks, seven JavaScript files and two JSON files. JavaScript regressions for pool choices, mean timings and missing samples pass, including after the latest UI text changes.
- Real authenticated LuCI interface checked: probe settings, health and certificate warnings, diagnostics, duplication preview, blocking during operation, duplication after stop, wildcard SNI route test and timing graph. New [screenshots](SCREENSHOTS.md) show monitoring and a graph with synthetic demo data.
- A 0.5.0 APK build with the official 25.12.5 SDK and package acceptance passed, including installation, runtime tests and removal. Local artifacts are in `artifacts/0.5.0-openwrt-25.12.5/`. Only UI help text, singular period labels and the accessible graph title changed afterwards. The final rebuild was not confirmed because Docker became unreachable; existing APKs must therefore not be treated as exact artifacts of this commit. The new byte comparison between package payload and source must still pass at the next build.

`sh tests/validate-0.5.sh` repeats Linux tests, OpenWrt tests, the official SDK build and package acceptance using the local test images. No new 24.10.5 IPK was built. The container uses the host kernel and cannot create procd cgroups; router and reboot acceptance remain separately required.

Nothing was installed or changed on the ASUS router. See [operation, settings and limitations](OPERATIONS.md).

## Stream extension — 0.4.0

Date: 28 September 2026.

- 116 unit/regression/shell contract tests passed.
- Both final 0.4.0-r1 APK packages built with the official OpenWrt 25.12.5 SDK for mediatek/filogic. Metadata and payload checked; installation, RPC/procd, firewall4, TCP/UDP forwarding over IPv4/IPv6, exact upstream byte volumes, rejected reload and removal using these APKs passed. Artifacts, checksums and reports are local in `artifacts/0.4.0-openwrt-25.12.5/`. No new 24.10.5 IPK built. The container cannot create procd cgroups; this host-environment limitation does not replace a router/reboot test.
- 10 shell scripts plus package hooks, 5 LuCI JavaScript files and 2 JSON files passed syntax checks; an additional JavaScript regression for pool choices in cloned edit dialogs passed.
- All seven existing Linux integration tests passed, including real transparent TCP/UDP connections over IPv4/IPv6.
- Ten new feature tests on Alpine/Nginx: backup on connection failure and a down primary, TLS/mTLS, upstream certificate verification and hostname rejection, SNI/ALPN routing, trusted PROXY/client-network routing, per-client connection limits and syntax of new balancers/socket options.
- The same ten tests ran on OpenWrt 25.12.5/Nginx 1.26.3. There, the real-IP test checks explicit rejection of the missing stream real-IP module; it does not prove address replacement on that build. The other nine tests pass positively. The first check found an undersized ARM map bucket; the generator was adjusted accordingly.
- Real LuCI 25.12.5 in a local container: sign-in, configuration loading, service editing, six tabs, TLS/preread and SNI selection, saving and reopening checked. This found and fixed an empty pool selector in edit dialogs and rejected RPC session metadata. The running manager and traffic graph were then checked through the authenticated LuCI session. Only the disposable container was changed; the test listener binds to loopback. [Screenshots](SCREENSHOTS.md) use synthetic traffic history.

The test environment uses the host kernel. No changes or reboot were performed on the ASUS router. See [compatibility and remaining limitations](STREAM-FEATURES.md).

Repeating the feature tests:

```sh
docker build -t nsm-test:0.4.0 -f tests/Dockerfile .
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  -v "$PWD:/src:ro" nsm-test:0.4.0 python3 tests/integration_features.py
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  -e NSM_EXPECT_NO_STREAM_REALIP=1 -v "$PWD:/src:ro" \
  nsm-openwrt-userland:25.12.5 python3 /src/tests/integration_features.py
```

The second command variant expects only the confirmed missing real-IP module in this OpenWrt feed. On a build with that module, omit `NSM_EXPECT_NO_STREAM_REALIP`. OpenSSL CLI is only a test dependency for generating temporary self-signed certificates.

## New traffic feature — 0.3.0

Date: 28 September 2026.

- 98 unit/regression/shell contract tests passed, including 11 new traffic-history tests: opt-in/privacy, retry/IPv6 association, partial lines, rotation, truncation/rewriting, invalid records, read/storage limits, RPC selection and atomic recovery.
- Syntax checks passed for 9 shell scripts plus package hooks, 5 LuCI JavaScript files and 2 JSON files.
- Both 0.3.0-r1 APK packages built with the official 25.12.5 SDK for mediatek/filogic; metadata and payload checked. Installation, runtime tests and removal using these APKs passed, including exact TCP/UDP byte volumes per address family. Local artifacts and build logs: `artifacts/0.3.0-openwrt-25.12.5/`. No new 24.10.5 IPK was built for 0.3.0.
- Six real Linux network/Nginx integration tests and one detached process contract test passed. The transparent TCP/UDP test over IPv4/IPv6 ran separately with `NSM_NETNS=1`. The first invocation lacked required container permissions for process inspection; rerunning with the documented permissions passed.
- Real OpenWrt 25.12.5 userspace: installation, rpcd/procd, firewall4, forwarding and counters for two IPv4 upstreams and one IPv6 upstream over TCP/UDP. The test also checks rejected reload and removal of an active installation.
- The real `traffic.js` view was checked in the browser through `tests/traffic-preview.html` with synthetic data and LuCI stubs. Period, protocol, direction, individual IPv6 upstream and empty/inactive history checked; no JavaScript console errors. This does not prove integration with an authenticated LuCI session or a specific router theme.

The log-based graph measures bytes at session completion, not live throughput. See [operation and capacity limits](TRAFFIC.md). The real router was not modified; reboot, kernel modules and full LuCI operation still require on-device acceptance.

## Earlier validation — 0.2.1

## Performed in the development environment

Date: 28 September 2026.

- 87 unit/regression/shell contract tests passed (65 existing + 22 new). Three new shell tests use backend/procd stubs and check start errors and cleanup order.
- All four LuCI JavaScript files checked with a JavaScript parser.
- POSIX shell syntax checked for installer, uninstaller, init service, RPC wrapper, maintenance, hotplug and package hooks; shell files use LF line endings.
- Menu and ACL JSON parsed.
- Six Linux integration tests with real Nginx, nftables, iproute2 and UCI passed.
- One separate process contract test for the detached service job, lock and error reporting passed; it uses a **stub init** and does not prove procd integration.

Tests cover:

- TCP, UDP, combined listeners and IPv6;
- backend pools, weights, backup restrictions and references;
- source-IP preservation with family checks and explicit return interfaces;
- overlapping listeners, direct self-loops and invalid ports;
- configuration injection through addresses, names and interfaces;
- preservation of other mark bits;
- constrained firewall input permits and snippet recovery;
- large logs, very long lines, missing files and invalid UTF-8;
- only permitted log paths and service actions;
- rejection before network mutation;
- recovery after a failed network update;
- confirmation of a new Nginx worker generation on reload;
- configuration and network recovery after a rejected reload.

Runtime commands in recovery tests are mocked. These tests establish Python control logic, not correct operation of a particular OpenWrt build or network topology.

Repeat from the project directory:

```sh
python3 -m unittest discover -s tests -v
python3 tests/check_static.py
```

The unit suite runs under Linux/WSL because the backend uses `fcntl` and Unix signals. Syntax checks require `sh` and `node`.

## Real Linux integration in 0.2.1

Test environment: Alpine 3.23 container, Nginx 1.28.3, nftables 1.1.5, Python 3.12.14, WSL2 kernel 6.18.33.2. UCI is built from upstream revision `74f6277aabffc943d026f406df57c22595134c42`; that revision is pinned in the Dockerfile. Alpine package feeds may supply newer package revisions in later builds.

Tested with real processes and sockets:

1. External TCP/UDP reuseport listeners are rejected by preflight.
2. Loopback and wildcard bind together for IPv4 and IPv6; TCP and UDP each reach the correct loopback or wildcard backend. A real HUP produces new workers.
3. Generated nftables rules are accepted by the kernel. Policy routes can be added, repeated and removed for both families; a foreign route remains intact.
4. Real UCI deltas are ignored by snapshot reads but block import. Importing the user fixture produces five services; disabled flags were read with the real UCI CLI. Backup bytes, an existing pool name with an apostrophe and duplicate import were checked.
5. An occupied new port passes `nginx -t` but causes a real rejected reload. The old listener keeps working; after restoring the old configuration, a checked recovery HUP succeeds. This tests Nginx mechanics; the complete Python/firewall4 rollback is tested separately with mocked commands.
6. Two separate network namespaces act as client and backend, with the container as router/proxy. Four request/reply tests (TCP/UDP × IPv4/IPv6) confirm that the backend sees the client IP and the client receives a reply. Generated return rules record packet hits. A separate INPUT filter admits backend TCP/UDP only through the NSM mark/socket rule. No NAT or flow offloading in this test.

A seventh test starts the real RPC CLI and a detached Python child process. A slow stub init deliberately fails; a concurrent action is rejected and the first error appears in `operation.json`. Only init is replaced; this remains a process contract test, not an OpenWrt end-to-end test.

Repeat on a computer with Docker, from the project directory:

```sh
docker build -t nsm-test:0.2.1 -f tests/Dockerfile .
docker run --rm --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
  -e NSM_NETNS=1 -v "$PWD:/src:ro" nsm-test:0.2.1
```

Use only a disposable container with its own network namespace, **no host networking and never the router**. The code refuses to run outside Docker. SYS_ADMIN is used for the two temporary test network namespaces and SYS_PTRACE for reading worker socket inodes; these are not new router package dependencies. Without SYS_ADMIN and `NSM_NETNS=1`, the narrower suite can run, explicitly skipping the transparent network test.

## New import tests in 0.2.0

The fixture `tests/fixtures/existing-nginx.conf` contains the stream configuration from the supplied guide. The test confirms five services, five pools, eight backends and three transparent services. TCP and UDP on the same address and port are merged into one combined service.

Also tested: quotes/comments in the parser, inherited timeouts, preservation of Nginx's default connection timeout, CIDR access rules, blocking includes/SSL/hostnames/unknown directives, UCI quoting, duplicate imports, backup contents, preservation of the source file and rejection of stale preview tokens. The fixture test is a conversion test, not a live network test.

## Real OpenWrt userspace in 0.2.1

The official ARM64 rootfs of OpenWrt 24.10.5 was tested locally with real UCI, ubusd, rpcd, procd and firewall4. Nginx 1.26.1-r2, Python 3.11.14-r1 and nftables 1.1.1-r1 come from the OpenWrt feeds. Installation, RPC signatures, start/reload/stop, TCP/UDP request/reply over IPv4 and IPv6, permit recovery after firewall reload, rejection of an invalid reload and a manual upgrade while active, and uninstall with configuration preservation all pass. After the rejected reload, both protocols and families still respond.

The container uses WSL2 host kernel 6.18.33.2; procd runs as a separate process, not PID 1. This does not prove OpenWrt boot or kernel-module behavior. See [BUILDING.md](BUILDING.md) for reproducible commands and separate SDK package inspection.

The same runtime test also passes on the extracted official ASUS TUF-AX6000 filesystem from OpenWrt 25.12.5 (`mediatek/filogic`), with Nginx 1.26.3-r4 and Python 3.13.9-r3 from the feeds. This also uses the host kernel; procd cgroup isolation does not work in this container. See the [target device details](ROUTER-ASUS-TUF-AX6000.md) for differences from installed router packages.

## SDK packages and package installation

On 28 September 2026, both packages (`0.2.1-r1`) were successfully built with the official SDKs:

| SDK | Artifacts | Package installation and runtime |
| --- | --- | --- |
| 24.10.5 x86/64 | Two IPKs, `Architecture: all` | Passed using real `opkg` in ARM64 24.10.5 userspace |
| 25.12.5 mediatek/filogic | Two APKs, `arch: noarch` | Passed using real `apk` in ASUS TUF-AX6000 25.12.5 userspace |

Inspection checks metadata, dependencies, root ownership, file permissions, LF line endings, a disabled default configuration, conffiles, hooks and all six LuCI files. This test found and fixed the missing buildroot signature in the LuCI Makefile.

For both formats, the actual built packages were installed, the RPC/TCP/UDP/firewall tests above were run and the packages were removed while the manager was active again. Runtime status and listener permits disappear; the modified UCI configuration is preserved byte for byte. The manual installer refuses to overwrite a package installation.

The SDK builds only the two script packages, with LuCI minification disabled. Runtime dependencies in installation testing come from the official feeds. This is not a complete rebuild of Nginx/Python/kernel modules. Logs, feed revisions, package manifests, configuration and checksums are stored locally under `artifacts/`; this directory is not tracked in Git.

## Still to validate on the target router

The original 0.2.1 validation used no real OpenWrt router or browser LuCI session. Additional container and browser checks for 0.3.0/0.4.0 are described above; target-router acceptance remains necessary:

- installation through target-device feeds;
- installation of the built packages on the target router;
- Nginx and nftables syntax checks on the **target OpenWrt version**;
- LuCI form behavior in the real browser;
- procd start/reload and a complete target-router reboot;
- TCP/UDP forwarding, IPv6 and transparent return routing on the **real router/topology**;
- interaction with mwan3/PBR, NAT, offloading and existing firewall rules.

## Router acceptance

1. Install on the target device. Check `ubus -v list luci.nginx-stream-manager` and that the LuCI menu appears.
2. Create an ordinary test service on an available high port, initially without source-IP preservation. Add one backend. Validate and start.
3. Test TCP with a real test connection and UDP with a request/reply test. Inspect both backend and client sides.
4. Add a second backend and check the selected balancing method. With client-IP hashing, do not expect one client to alternate between both backends.
5. Test optional firewall permits from both an allowed and a disallowed interface or source address.
6. Configure transparent mode only after checking the return route. Check the source IP received by the backend and counters in `nft list table inet nsm`.
7. Repeat with an IPv6 service if applicable.
8. Deliberately create an invalid backend reference and apply it. The old active configuration must keep running and Overview must show the error.
9. Occupy a new listening port with another process and try adding it through a reload. Check that the reload is rejected and the old configuration returns.
10. Reload firewall4 and refresh an interface. Check permits, return marking and policy routes.
11. Stop the manager. Its own routing rules and active permits must disappear; other rules must remain.
12. Test startup after reboot once both the manager and boot start are explicitly enabled.

13. Create a pool, select Save, then add a backend and service and check that the new pool is selectable. After import, also check a newly opened configuration view and an old browser session.
14. For service buttons, first check `operation.pending`, then completion/error. An accepted request is not yet a successful start. Test a rejected start and a failed firewall reload; inspect `last_error`, the actually active rules and any `pending.json`.
15. Test manual reinstallation and package upgrade separately, preserving UCI and boot preference. Check uninstall for leftover rules; dependencies remain after manual uninstall. First build both packages in the matching SDK.

The installer does not activate the manager. Existing manual Nginx listeners must be migrated before taking over their ports.

## Next concrete router test

Start with read-only inventory before installation or activation:

```sh
ubus call system board
uname -r
nginx -V 2>&1
```

The ASUS TUF-AX6000 inventory has been received: OpenWrt 25.12.5, `mediatek/filogic`, kernel 6.12.94 and Nginx 1.26.3. Matching SDK and container commands are in [BUILDING.md](BUILDING.md). The first on-device runtime test uses an available high port and one controlled TCP/UDP backend with source-IP preservation disabled. Only after installation, `ubus -v list luci.nginx-stream-manager`, LuCI Save & Apply and start/stop pass should the transparent test follow with a verified return route. Ports 53 and 51900 remain with the existing setup during that first test.
