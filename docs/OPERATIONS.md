# Monitoring and management — 0.6.0

This version adds management features around the free TCP/UDP stream module. The Nginx baseline remains 1.26.3. No HTTP reverse-proxy manager has been added.

## Upstream health

Choose an active check for each backend under **Configuration → Backend servers → Edit**:

- **TCP connect**: the port accepts a connection. This does not prove application health.
- **Verified TLS handshake**: a handshake with mandatory certificate-chain and hostname verification. Specify the expected TLS hostname; an optional CA file replaces the system trust store.
- **DNS query over UDP**: sends an A query to the configured backend port. The reply must confirm the question and transaction ID and indicate NOERROR. This is a DNS-specific check, not a general UDP health check.

Probes use backend settings, never an arbitrary destination supplied through RPC. They run from the router; a different network path or source IP from the actual transparent proxy connection may produce a different result. Backup and administratively down backends can also be checked.

All checks are disabled by default. They do not change pool membership or Nginx configuration. Failover remains Nginx's existing passive feature. Health status in this manager is not Nginx's internal passive status.

Global settings: interval of 30–3600 seconds, 1–10 consecutive failures before down and 1–10 successes before up. The maintenance loop checks at most 16 backends per round, with four concurrent subprocesses. Each probe has a hard subprocess limit of four seconds, including DNS resolution. With many backends, a complete round takes longer than the configured interval; the oldest results take priority. The UI explicitly shows unknown/stale results.

The automatic loop runs while the manager is running. **Monitoring → Run due checks** also works with a stopped manager. The button respects the interval and prevents overlapping rounds.

## Alerts and certificates

Monitoring shows active alerts, up to 20 recent events and paginated backend/certificate results. At most 100 events are retained in RAM. Recovery produces one recovery notification. Optionally, changed alerts and recovery notifications are sent through `logger` to OpenWrt syslog; existing remote-syslog configuration can process them further. No email/webhook service is configured.

Alerts cover backend-down status, certificate problems/expiry, the last failed manager action and unavailable archive storage. Identical alerts are not logged again every round. Event history starts over after reboot.

Existing `ssl_certificate` and `proxy_ssl_certificate` files are inspected locally. The UI shows subject, issuer, names, validity dates and remaining days; output is bounded. Private keys are never returned. At most four certificates are read per round; each file is checked again approximately every hour. The warning threshold is configurable from 1–90 days, default 30. File inspection checks the first certificate and does not prove a complete chain or a match with the private key. The verified TLS probe and `nginx -t` provide additional checks.

## Graphs and export

In addition to sent/received bytes, **Traffic** shows:

- upstream connection attempts;
- failed attempts or a final 500/502/503 status;
- retries from an upstream to the next one;
- mean measured connection time;
- mean session duration, attributed to the last selected upstream.

All measurements come from **completed** sessions. A missing upstream connection time counts as a failed/unmeasured attempt; the graph is not a live connection or packet-loss meter. Session duration may include earlier retries. Historical 0.3/0.4 logs lack error/timing measurements; missing timing samples are not averaged as zero. Sessions in which no upstream address was selected have no per-upstream entry.

CSV export contains the currently displayed series and raw sums/counts. Filters also apply to export. The number of graph series is reduced if necessary to fit the RPC output limit. The selector lists the 64 largest upstreams; other addresses can be requested explicitly through the existing `traffic` RPC.

**Monitoring → Export Prometheus metrics** provides the monitoring text format. Automation can read the existing authenticated `metrics` RPC or use `ubus call luci.nginx-stream-manager metrics` locally. No public HTTP metrics endpoint has been added. An external collector must process the JSON `text` property as Prometheus text. The 24-hour traffic values are rolling-window **gauges**, not monotonic counters. Probe timestamps identify stale measurements. Output is bounded; `nsm_traffic_omitted_series` and `nsm_backend_omitted` report omitted series. CSV/RPC export remains available for individual upstreams.

## History on a mounted drive

Set **Persistent history directory** to a path such as `/mnt/usb/nsm`. The drive must already be mounted. The application does not mount or format drives. Symlink paths and paths outside `/mnt/<mount>/<directory>` are rejected. If the mount is missing, the application does not write a fallback archive to router flash; RAM collection continues and reports the storage problem.

The archive contains up to 30 days of hourly aggregates, a checkpoint of recent RAM measurements and read positions. Retention is 1–30 days, default 7. Atomic writes occur at most once every five minutes. After reboot, the latest checkpoint is restored once the manager runs with this storage setting. The last five minutes may be lost. Stopping/removing the application does not delete the archive.

Limits: 8 MiB per archive file, 32,768 hourly cells, plus existing RAM limits of 256 series and 8,192 minute cells. Many services/upstreams shorten effective history. Atomic writes temporarily require space for two files. The 7- and 30-day periods use this archive and may lag by up to five minutes. A full or missing drive does not block the proxy.

## Configuration history, restoration and duplication

**Configuration tools** supports:

1. A manual snapshot of the saved UCI configuration.
2. Automatic snapshots of configurations for which Nginx workers were observed after start/reload. This does not prove end-to-end forwarding.
3. A diff against a selected snapshot.
4. Export/import of an application-specific `nsm-config-1` JSON backup.
5. Copying a service with its routing rules, or a pool with all its backend entries. New services remain disabled.

The latest ten distinct snapshots are retained under `/etc/nginx-stream-manager/history`, with at most 32 KiB of configuration per snapshot. This survives reboot and writes only when configuration/status changes. Snapshots and external archives are preserved on uninstall. Backups contain all manager settings and certificate paths, but no certificates, keys, other Nginx files or global OpenWrt network configuration.

Restore/import/duplicate first show a proposal. The preview token expires after ten minutes and becomes invalid when the saved configuration changes. The actual commit requires a stopped manager, no running service action and no unsaved LuCI changes. The backend validates UCI, Nginx and nftables before replacement and saves the previous configuration. Failed validation leaves the current configuration in place. Restart explicitly through Overview afterwards. A truncated diff cannot be committed through the UI.

## Wildcard SNI and diagnostics

Routing rules support exact names and `*.example.com`. Priority: exact name, then the longest matching wildcard suffix, then the default pool. `*.example.com` matches subdomains, not `example.com` itself. Regular expressions and suffix wildcards such as `mail.*` are not offered. The SNI test field uses the saved configuration and the same priority rules. See [Nginx map priorities](https://nginx.org/en/docs/stream/ngx_stream_map_module.html).

**Monitoring → Diagnostics** shows the Nginx build, listening sockets, policy rules/return tables, limited DNS checks for configured hostname backends and syntax probes of the actually installed stream module. A module flag in `nginx -V` alone does not prove support. The diagnostics page does not configure routes and cannot establish whether an external backend actually sends replies back through the router.

## Renaming pools and services — 0.6.0

A **display name** is the human-readable label edited under **Configuration → Backend pools / Services → Edit → Display name**. Changing this label keeps the section ID and traffic-history identity unchanged. Save and apply it through the normal configuration form.

A **section ID** is the unique identifier shown in the Name column and used by references, RPC requests, generated configuration and service log/history keys. To change it:

1. Save or discard pending configuration edits in all LuCI sessions, then stop the manager from **Overview**.
2. Open **Configuration tools → Rename or duplicate service or pool**.
3. Select the existing pool or service and enter a new unique ID: 1–48 letters, digits or underscores. The service ID `manager` is reserved.
4. Select **Preview rename** and review the diff. Preview alone changes no saved configuration.
5. Select **Commit previewed configuration**. Validation and a backup run before the file is replaced. A stale preview, a running manager or a pending service action blocks commit.
6. Refresh any other open configuration views, review the settings and explicitly start the manager.

Renaming a pool updates its backend references, default service pools and route destinations. Renaming a service updates route ownership. Disabled entries are included. Existing display names, enabled flags, backend IDs and other settings remain unchanged.

Service logs and traffic history are not moved to the new ID. New completed sessions use the new service identity; old records retain the old one and expire under normal retention rules. Update external scripts or monitoring queries that refer to the old ID. Use a display-name edit when you only want a different label without changing these identities.