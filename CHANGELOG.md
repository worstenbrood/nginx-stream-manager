# Changelog

## 0.2.1 — 2026-09-28

- Real Linux integration tests with Nginx, nftables, UCI and separate client/backend network namespaces: TCP/UDP, IPv4/IPv6 and client-IP preservation.
- Fixed invalid nftables block closure in preflight; the real validator now runs in integration tests.
- Committed UCI is read through a unique snapshot package; `uci -P` alone did not exclude existing deltas. Init now also reads this saved state.
- External listener conflicts are checked before apply, including dangerous `reuseport` overlap and potential IPv6 dual-stack overlap.
- Start confirmation, idempotent start, cleanup after process termination and recovery-reload checks. An apply journal retains cleanup information after partial failures.
- Firewall recovery actually reloads the previous rules. Routing conflicts are checked before changes; no more `route replace`, and narrower deletion of application-owned routes/rules.
- Background actions for RPC service management with completion/errors in Overview; stricter argument types and a total response limit.
- Import blocks duplicate/invalid directives and conditional logs that cannot be preserved. Backup preserves exact bytes; snapshot and preview token use the same application contents.
- LuCI pool choices refresh after reload. Maintenance retries busy hotplug recovery.
- Installer preserves the existing Nginx provider and refuses to mix with package management. Package hooks report errors, preserve boot preference on future upgrades and reload after removal. Versions and architecture declarations updated.
- README, review and test procedure distinguish process confirmation, best-effort recovery, Linux integration and still-missing OpenWrt acceptance.

## 0.2.0 — 2026-09-28

- New LuCI import view for `/etc/nginx/nginx.conf`.
- Conservative parser for inline stream/upstream/server configuration.
- Preview with blocking issues and warnings; no silent partial import.
- Source- and destination-bound preview token to prevent stale confirmations.
- Backup before import; all imported services remain disabled.
- Detection of repeated import of the same source contents.
- Existing application sections are preserved; existing Nginx is unchanged.
- Support for specific and wildcard listeners on the same port, needed for loopback resolvers alongside LAN listeners.
- 23 new import tests; 65 tests in total.

## 0.1.0 — 2026-09-28

- Generic TCP/UDP services, backend pools, transparent return routing, firewall permits, logs, procd service and LuCI views.
