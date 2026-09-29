# Changelog

## 0.6.1 — 2026-09-29

- Rename individual backend section IDs, including generated import IDs, through the existing preview and commit workflow.
- Show backend pool, address and port in the section selector. Backend renames preserve all settings and do not rename their pool or service.
- Disable duplication for individual backends; pool and service duplication remain available.
- Current configuration exports use the new backend ID. Existing snapshots and events keep their historical names; health checks start fresh, while traffic history retains its service/upstream-address identity.
- Add backend rename/export regression coverage and extend OpenWrt package acceptance to rename a backend before testing forwarding.

## 0.6.0 — 2026-09-29

- Rename pool and service section IDs through Configuration tools, with preview, stale-token protection, validation and a backup before commit.
- Pool renames update backend, service and routing-rule references; service renames update routing-rule references, including disabled entries. Existing display names and enabled states are preserved.
- Reject duplicate, invalid and reserved service IDs. Renaming requires a stopped manager and no pending configuration edits or service action.
- Refresh configuration and SNI selectors after committing changes. Clarify the difference between editable display names and section IDs.
- Service logs and traffic history retain their original identity; external scripts must use the new ID. No automatic history migration or service restart.

## 0.5.0 — 2026-09-28

- Monitoring with optional TCP, verified TLS and DNS/UDP probes, bounded concurrency, consecutive success/failure thresholds and explicit stale results. Probes do not change forwarding.
- Local alerts and optional syslog notifications for health, certificates, manager errors and archive storage; certificate names and expiry dates visible in LuCI.
- Graphs for failed attempts, retries, connection time and session duration; CSV export and authenticated Prometheus text export.
- Optional mounted storage with checkpoints and up to 30 days of hourly history. Bounded storage; a missing mount does not fall back to router flash.
- Ten configuration snapshots, diff view, JSON export/import and service/pool duplication. Restore requires a fresh preview, a stopped manager and successful validation; no automatic restart.
- Wildcard SNI with exact/longest-wildcard/default priority and a test field; diagnostics for modules, sockets, DNS and return routing.
- New regressions and real TCP/TLS/DNS and Nginx wildcard tests; extended OpenWrt RPC acceptance.

## 0.4.0 — 2026-09-28

- Backup/down directly visible, working backup when the primary server is administratively down, additional hash/random balancers and configurable shared-memory zones.
- DNS backends at start/reload and UNIX upstreams; transparent configurations remain restricted to literal IP addresses.
- TLS termination, client certificates, upstream TLS with certificate verification enabled by default, TLS preread and routing by SNI/ALPN/TLS version/client network.
- Incoming PROXY v1/v2, trusted proxy CIDRs, deny CIDRs, service/client connection limits, source-address binding, retry budget and socket options.
- Service settings divided into six tabs; additional logging and worker settings.
- Real Nginx/OpenWrt feature tests; corrected ARM SNI map size. Clear validation error when the installed stream module lacks real IP, even if nginx -V lists that option.
- Feature matrix documents supported capabilities, version requirements and configuration forms not yet managed.
- Fixed LuCI pool choices in edit dialogs; RPC accepts session metadata sent by authenticated LuCI requests.
- Added screenshots of the real LuCI interface, including a traffic graph with synthetic demo data and a repeatable local preview environment.

## 0.3.0 — 2026-09-28

- New Traffic view with graphs per load balancer/upstream, sent and received bytes, TCP/UDP filter and periods of 1, 6 or 24 hours.
- Optional session measurements without client IP addresses, independent of ordinary access logs. Bytes are added to the time graph at session completion; not a live bandwidth meter.
- Bounded RAM history, incremental log processing, support for retries, IPv6, rotation and partial lines; notification of incomplete data.
- New regression tests and real OpenWrt traffic measurements across multiple upstreams. LuCI view checked visually in isolation with demo data.

- Fixed the missing OpenWrt buildroot signature in the LuCI Makefile; without that line the SDK could not find the frontend package.
- Added a repeatable SDK build, IPK/APK content inspection and read-only router inventory targeting the ASUS TUF-AX6000 with OpenWrt 25.12.5.
- Tested real OpenWrt 24.10.5 and 25.12.5 userspace with rpcd/procd, TCP/UDP over IPv4/IPv6, rejected reload, firewall4 and installation/removal; hardware and boot tests remain separately required.

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
