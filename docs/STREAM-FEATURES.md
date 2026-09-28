# Stream features and compatibility — 0.4.0

0.5.0 update: wildcard SNI (`*.example.com`), active manager probes, certificate status, extended measurements, archive storage and configuration management are covered in [OPERATIONS.md](OPERATIONS.md). These extend the original 0.4.0 limitations below regarding exact SNI names and passive-only monitoring; Nginx version requirements still apply.

This extension targets the free TCP/UDP features of **Nginx 1.26.3**, the version on the ASUS TUF-AX6000. The application is not an HTTP reverse-proxy manager. Nor is it an editor for every possible Nginx directive: the table below explicitly describes supported features and remaining limitations.

## Available in the manager

| Component | Features |
| --- | --- |
| Backend pools | Weights, shared upstream zone, round-robin, least-connections, random, random-two-least-connections, ordinary/consistent hashing |
| Hash keys | Client IP, client IP/port, listening IP/port, SNI during TLS inspection or termination, PROXY client address |
| Failover | Backup servers, down status, passive failure detection, max_fails, fail_timeout, max_conns, retries on/off, maximum attempts and total retry time |
| Backend addresses | IPv4, IPv6, DNS names at start/reload, UNIX stream sockets |
| Pool routing | Exact SNI hostname, ALPN token, TLS version/non-TLS, client IP/CIDR; default pool as fallback |
| Client TLS | Termination, TLS 1.2/1.3, certificate/key, client certificates, CA/CRL, ALPN, ciphers/curves/DH, session cache, tickets and time limits |
| Upstream TLS | TLS versions/ciphers, explicit SNI/certificate name, CA verification enabled by default, client certificate/key, CRL, session reuse |
| TLS passthrough | ClientHello inspection without decryption through ssl_preread |
| PROXY protocol | Outgoing v1; incoming v1/v2 required per TCP listener; optional trusted proxy CIDRs with the real-IP module |
| Access and limits | Allow/deny CIDRs, service and client limits, dry-run, configurable zone memory, upload/download rate limits per connection |
| Networking | Optional client-IP preservation, explicit local source address, firewall input permits and return routing for all routed pools |
| Socket/proxy | Buffer sizes, preread timeout, backlog, TCP Fast Open, deferred accept, listener/upstream keepalive, TCP_NODELAY, half-close, UDP request/response limits |
| Management | Worker/FD limits, graceful-shutdown deadline, multi_accept, accept_mutex, log level, access logs, rotation and traffic history |

Service options appear in the edit dialog under **General**, **Network and access**, **Proxy and failover**, **Logging**, **Listener and limits**, **TLS and routing**. Save pools and services before selecting references in other tables.

## Backup servers

Under **Backend servers**, **Backup server** and **Administratively down** appear directly in the table. Backup works with round-robin and least-connections; Nginx does not support it with hash/random. A pool must contain a primary server, but that server may be down as long as an available backup remains.

Failover applies when establishing new upstream connections. An existing session is not moved to another server. Passive failure detection is not an active health check. UDP in particular has limited failure detection without application-protocol checks; silence from a UDP server does not prove it is offline. With a single server, Nginx ignores certain passive failure parameters.

## TLS and routing

For passthrough, select **Inspect ClientHello**. Under **Pool routes**, add a mapping such as `app.example.test → web_tls_pool` and select SNI routing. Unknown names go to the service's normal pool. The backend performs the TLS handshake; the router does not need a certificate for this.

For termination, select **Terminate TLS** and provide existing absolute certificate/key paths on the router. The application does not read or upload key contents through LuCI or request certificates. Nginx checks the files during validation. For encrypted upstream connections, specify **Upstream TLS certificate/SNI name** and a trusted CA file. That name applies to all pools the service can select. Disabling verification produces a warning.

ALPN inspection may see multiple protocols offered by the client: the first matching routing rule wins. During termination, routing uses the protocol actually negotiated. Client-network routing selects the most specific CIDR. TLS version match `none` is intended for non-TLS traffic during preread. SNI matches are exact names, not wildcard/regex configuration. Client-IP hashing behind a proxy uses the real client address only when the trusted real-IP feature is present and configured.

Client TLS, upstream TLS, PROXY protocol and TCP-specific listener options require a TCP-only service. Create separate TCP and UDP services when these features are needed. UNIX upstreams require TCP and sufficient socket/directory permissions for the Nginx worker. DNS backends are resolved at start/reload, not automatically when DNS changes later. Use literal IP addresses when direct self-loop detection or transparent return routing is needed.

## OpenWrt module checks

The tested official 25.12.5 feed supplies a `nginx-full` that lists **stream_realip** in `nginx -V`, while the accompanying `nginx-mod-stream` does not contain that module. It rejects `set_real_ip_from`. The manager reports this specifically and does not apply the configuration. Trusted PROXY client addresses have been tested on Alpine with a complete stream module. Incoming PROXY headers without address replacement can be used on OpenWrt, but access control/hashing then sees the proxy address.

A matching stream module with real-IP support is required for this; the application does not automatically replace your Nginx packages. For other optional modules too, the actual `nginx -t` check is decisive. With trusted proxies, the managed firewall uses proxy CIDRs as the network source; Nginx allow/deny rules then use the restored client address.

## Limitations of this version

| Feature | Reason/limitation |
| --- | --- |
| Active health checks, slow_start, commercial management API, upstream state/session_drop | Not free Nginx 1.26.3 features |
| Dynamic DNS upstreams / SRV resolution | Free only since 1.27.3; static DNS at reload is available |
| Stream OCSP/stapling, certificate cache | Require newer Nginx versions (1.27.2/1.27.4) |
| least_time, upstream ALPN, outgoing PROXY v2 | Not available as free features on the 1.26.3 baseline |
| Arbitrary map/geo/split_clients programming, njs/Lua, arbitrary includes/OpenSSL commands | Not a general scripting/configuration editor; safe, fixed routing and TLS choices are described above |
| UNIX listeners, port ranges, multiple virtual servers on one IP/port, static return responses | Not yet supported by the listener/firewall model; use separate IP/port services for managed listeners |
| Certificate issuance, ACME, hardware-engine/PKCS#11 keys | Not a certificate manager; existing PEM files are used |
| Multiple certificate pairs per listener, arbitrary custom log formats and variable rate limits | Not in this UI version; one certificate pair, a fixed log format and a fixed limit per service |

Import remains conservative. New advanced configurations are not automatically converted back from arbitrary Nginx text; unsupported import directives block import so that behavior is not silently lost.

## Sources

- [Upstreams and balancing](https://nginx.org/en/docs/stream/ngx_stream_upstream_module.html)
- [Proxy, upstream TLS and retries](https://nginx.org/en/docs/stream/ngx_stream_proxy_module.html)
- [Listeners and stream core](https://nginx.org/en/docs/stream/ngx_stream_core_module.html)
- [TLS termination](https://nginx.org/en/docs/stream/ngx_stream_ssl_module.html)
- [TLS preread](https://nginx.org/en/docs/stream/ngx_stream_ssl_preread_module.html)
- [Real IP from PROXY protocol](https://nginx.org/en/docs/stream/ngx_stream_realip_module.html)
- [Connection limits](https://nginx.org/en/docs/stream/ngx_stream_limit_conn_module.html)

Current Nginx documentation also describes newer releases. The application targets the baseline above; consult the version annotations for each directive.
