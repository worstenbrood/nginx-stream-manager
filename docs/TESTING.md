# Validation and test status

## Performed in the development environment

Date: 28 September 2026.

- 65 Python unit tests passed.
- All four LuCI JavaScript files checked with a JavaScript parser.
- POSIX shell syntax checked for installer, uninstaller, init service, RPC wrapper, maintenance and hotplug.
- Menu and ACL JSON parsed.

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
```

## New import tests in 0.2.0

The fixture `tests/fixtures/existing-nginx.conf` contains the stream configuration from the supplied guide. The test confirms five services, five pools, eight backends and three transparent services. TCP and UDP on the same address and port are merged into one combined service.

Also tested: quotes/comments in the parser, inherited timeouts, preservation of Nginx's default connection timeout, CIDR access rules, blocking includes/SSL/hostnames/unknown directives, UCI quoting, duplicate imports, backup contents, preservation of the source file and rejection of stale preview tokens. The fixture test is a conversion test, not a live network test.

## Still to validate on OpenWrt

No real router, browser LuCI session, Nginx binary, nftables kernel or SDK was available during development. The following checks have therefore explicitly not yet been performed:

- installation through target-device feeds;
- actual `.apk`/`.ipk` package builds;
- Nginx and nftables syntax checks on the target version;
- LuCI form behavior in the real browser;
- procd start/reload/reboot;
- TCP/UDP forwarding, IPv6 and transparent return routing with real packets;
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

The installer does not activate the manager. Existing manual Nginx listeners must be migrated before taking over their ports.
