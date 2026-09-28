# Changelog

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
