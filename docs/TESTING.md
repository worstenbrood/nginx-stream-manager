# Validation and test status — 0.2.1

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

## Still to validate on OpenWrt

No real OpenWrt router, browser LuCI session or SDK was used. The following checks have therefore explicitly not yet been performed:

- installation through target-device feeds;
- actual `.apk`/`.ipk` package builds;
- Nginx and nftables syntax checks on the **target OpenWrt version**;
- LuCI form behavior in the real browser;
- procd start/reload/reboot;
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

This allows selection of the correct SDK, package feeds and installed Nginx variant. Then build 0.2.1 for that release. The first runtime test uses an available high port and one controlled TCP/UDP backend with source-IP preservation disabled. Only after installation, `ubus -v list luci.nginx-stream-manager`, LuCI Save & Apply and start/stop pass should the transparent test follow with a verified return route. Ports 53 and 51900 remain with the existing setup during that first test.
