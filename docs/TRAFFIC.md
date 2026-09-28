# Traffic graphs — 0.3.0

## Usage

1. Open **Services → Stream Manager → Configuration**.
2. Enable **Traffic history** for each service you want to measure; this option is disabled by default.
3. Select **Save & Apply** and start/reload the manager from **Overview**.
4. Open **Traffic**. Select the load balancer, period, TCP/UDP, upstream and direction.

By default, the eight upstreams with the most sent bytes are shown. Each retained upstream can also be selected individually. The table shows bytes sent to and received from the upstream, the number of upstream attempts and the share of **displayed sent bytes**. A retry counts as a separate attempt. IPv4 and IPv6 are supported. A shared pool is measured separately for each service; TCP and UDP can be filtered separately.

## What the graph measures

Nginx writes upstream byte counters when a stream session ends. All bytes for that session appear in the minute of **completion**. Long-running TCP connections are therefore visible only afterwards. For UDP, Nginx determines when the session ends using its session timeout and configured request/response limits.

The graph shows volumes in B/KiB/MiB/GiB, not current bit/s. A peak may represent a long connection that just ended. An empty interval does not prove there was no traffic. The current interval is incomplete. This is not a billing or packet measurement: protocol overhead outside Nginx's upstream counters is excluded.

| Selected period | Bucket size |
| --- | --- |
| 1 hour | 1 minute |
| 6 hours | 5 minutes |
| 24 hours | 15 minutes |

The view refreshes every 10 seconds. The background task collects every 30 seconds; a request from the view also collects data. **Collector started** is the collector start time for the entire manager, not the time each service was enabled.

## Storage, privacy and limits

The separate `traffic.log` contains the service ID, protocol, completion time, upstream address and byte counters. It contains no client addresses. Ordinary access logs have their own setting and may contain client addresses.

The collector stores cursors and minute aggregates in `/var/run/nginx-stream-manager/traffic.json`; logs are under `/var/log/nginx-stream-manager`. On a normal OpenWrt layout, these locations are in RAM. History is lost on reboot and extends back at most 24 hours. The collector retains at most 256 service/protocol/upstream combinations and 8,192 minute cells globally. With many active upstreams, available history may therefore be shorter. The oldest cells are removed first.

Each collection reads at most approximately 2 MiB. Lines longer than 4 KiB and invalid measurements are skipped. Maintenance rotates the traffic log above 512 KiB and retains one predecessor. This is a **periodic rotation threshold**, not a hard cap: the file can grow larger between checks. At high log rates, processing may fall behind or history may be lost. The view reports backlogs, detected missing/invalid data and capacity overruns. The absence of a warning does not guarantee complete records after crashes or external log changes.

Disabling the feature stops new records after reload; previously collected history expires naturally. Without an active manager, the page still collects available log records. It does not reconstruct sessions from before the feature was enabled.

## Validation

Tests cover increments, retries, partial lines, rotation, truncation, invalid records, read/storage limits, selection and RPC arguments. In real OpenWrt userspace, TCP/UDP echo connections are made to two IPv4 upstreams and an IPv6 upstream, and reported counters are checked. Browser validation uses the real view with synthetic data; full LuCI operation on the target router remains a separate acceptance check.
