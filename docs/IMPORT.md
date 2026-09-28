# Importing from /etc/nginx/nginx.conf

## What the import does

The importer reads one fixed source path: `/etc/nginx/nginx.conf`. It does not accept arbitrary paths from the browser. The file must be at most 256 KiB and contain one inline `stream { ... }` block.

A preview shows the listeners, protocols, proxy destinations and source-IP settings found. Blocking issues prevent any import; a partial import is never silently applied.

After explicit confirmation:

1. The importer rereads the source and application configuration.
2. The preview token must still match both files.
3. The manager must be stopped and `global.main.enabled` must be `0`.
4. There must be no visible uncommitted CLI UCI changes.
5. The existing application configuration is backed up to `/etc/nginx-stream-manager/import-backups/`.
6. Pools, backends and **disabled** services are added to `/etc/config/nginx_stream_manager`.
7. Nginx, the firewall and networking are not started, reloaded or changed.

The application retains at most twenty import backups before refusing another import. Archive older backups manually at that point. Existing application sections are preserved, but the application configuration file is serialized again: comments and original formatting remain in the backup. The original Nginx file is never rewritten.

Save or discard changes in other open LuCI tabs before importing. The importer cannot inspect every separate LuCI session buffer. Avoid concurrent manual edits during import.

Since 0.2.1, the bytes used for the preview token are also passed to UCI through a unique temporary package. This prevents CLI deltas from entering the import. Backups are byte-identical, including line endings. The final check before file replacement reduces the race window but is not an atomic compare-and-swap with external editors or rpcd. Only application operations share the application's lock. A writer acting immediately after the final check can still conflict; do not edit the configuration concurrently. Import clears the LuCI cache for the current view, not for other browser sessions.

## Supported conversions

| Nginx | Application |
|---|---|
| `upstream name { server IP:port; }` | Pool and backend servers |
| No explicit balancing method | Round-robin |
| `least_conn` | Least connections |
| `hash $remote_addr consistent` | Client-IP hash |
| Backend `weight`, `max_fails`, `fail_timeout`, `max_conns` | Corresponding backend options |
| Backend `backup`, `down` | Backup/administratively down |
| `listen IP:port` or `listen port` | TCP listener |
| `listen ... udp` | UDP listener |
| TCP and UDP on the same endpoint in one server block | Combined service |
| `proxy_pass pool` | Reference to an imported pool |
| `proxy_pass IP:port` | Automatically created pool with one backend |
| `proxy_bind $remote_addr transparent` | Source-IP preservation enabled; return interfaces still required |
| `proxy_bind off` | Ordinary forwarding |
| `proxy_timeout`, `proxy_connect_timeout` | Timeouts, including seconds/minutes/hours/days |
| `proxy_requests`, `proxy_responses` | UDP options |
| `proxy_upload_rate`, `proxy_download_rate` | Rate limits |
| `proxy_next_upstream_tries` | Maximum attempts |
| `proxy_socket_keepalive`, `proxy_half_close` | TCP options |
| `proxy_protocol on/off` | PROXY protocol to a TCP backend |
| `allow CIDR; ... deny all;` | Allowed client networks |
| `access_log off` or an access-log path | Access logging disabled/enabled at the application's own log path |

Stream-level settings are inherited; explicit server settings take precedence. When `proxy_connect_timeout` is absent, the application imports **60 seconds**, the Nginx default, instead of the 5 seconds used by the form for a new service.

The application keeps its own worker settings, log files, log format and upstream zones. The preview reports this. General Nginx `http` and `events` blocks are not converted to stream services.

## Blocking cases

- Top-level or stream `include`: this version does not follow external files.
- TLS termination with `listen ... ssl`, SNI routing, maps or unknown stream/server directives.
- Hostname or UNIX-socket backends.
- Hash expressions other than `$remote_addr consistent`.
- A fixed `proxy_bind` source or preservation of the client's source port.
- Complex allow/deny ordering that cannot be represented as an allowlist followed by deny-all.
- Duplicate identical listeners, incorrect IP families or invalid values.
- A source file already imported with the same contents.
- Source or application configuration changed since the preview.
- Duplicate single-value directives, multiple balancing methods or directives at an unsupported level.
- Conditional access logs (`if=...`): the application cannot preserve the condition.

The importer does not execute the source file or use shell evaluation. The text parser checks block structure and supported conversions; normal runtime validation with Nginx and nftables is still required before activation.

## User-supplied configuration

The fixture test for the supplied configuration expects:

| Imported service | Protocol | Destination |
|---|---|---|
| `0.0.0.0:51900` | UDP | WireGuard pool |
| `127.0.0.1:53` | TCP + UDP | `127.0.0.1:54` |
| `0.0.0.0:53` | TCP + UDP | IPv4 DNS pool |
| `[::1]:53` | TCP + UDP | `[::1]:54` |
| `[::]:53` | TCP + UDP | IPv6 DNS pool |

Pool names are retained as display names. Technical UCI IDs receive an `imp_...` prefix to avoid collisions with existing manually created names.

All five services remain disabled after import. The three transparent services still require return interfaces and routing confirmation. The old sysctls, kernel settings and TPROXY includes from the guide are not automatically imported or removed; the application manages its own socket-based return routing.

## Restoring an import backup

Stop the manager, select the specific backup path shown by the import and copy it to `/etc/config/nginx_stream_manager`. Reopen LuCI. Start the manager only after checking the configuration. Import did not change your old `/etc/nginx/nginx.conf`.
