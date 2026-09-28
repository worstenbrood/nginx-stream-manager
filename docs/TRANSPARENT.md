# Transparent proxying and return routing

## Data path

1. A client connects to a listening address on this router.
2. Nginx accepts the TCP connection or UDP session.
3. Nginx creates a separate upstream socket. With `proxy_bind $remote_addr transparent`, that socket uses the client's IP as its source address.
4. The backend replies to that client IP and the source port chosen by Nginx.
5. The reply must pass through the same router. The nftables socket match recognizes the local transparent upstream socket.
6. The application sets only its own mark bit and routes this packet locally to Nginx.
7. Nginx returns the reply through the client session.

This forwards traffic to explicit local listeners. Transparent upstream binding does not automatically make Nginx listeners capable of handling arbitrary intercepted transit packets. No general `dport ... tproxy to ...` rules are generated.

## Kernel modules

`kmod-nft-socket` and the associated `kmod-nf-socket` support provide socket matching. The package manager resolves additional kernel dependencies.

`kmod-nft-tproxy`, `kmod-nf-tproxy`, `kmod-ipt-tproxy` and `iptables-mod-tproxy` are not installed: this implementation does not use the TPROXY target or mix iptables rules with firewall4.

Nginx sets the relevant socket option with `transparent`. The application therefore does not blindly enable `ip_nonlocal_bind=1`. Nor does this setup without transparent loopback backends require `route_localnet=1`. The master starts as root; on Linux, Nginx passes the capability needed for transparent binding to the workers, which run as `nobody:nogroup` here.

## Rules

Example of a return rule constrained by the generator:

```nft
iifname "br-backend" ip saddr 192.0.2.10 tcp sport 9000 socket transparent 1 meta mark set meta mark | 0x40000000 counter accept
```

Important details:

- The backend source port is the service port on the backend, not the public listening port.
- The socket match must succeed; the rule does not intercept arbitrary backend packets.
- Other packet-mark bits are preserved.
- The application does not blindly copy conntrack marks to all packets.
- There is no broad exemption for all root traffic.

The corresponding routing rules apply only to IP families actually used by transparent services:

```sh
ip -4 rule add priority 10100 fwmark 0x40000000/0x40000000 lookup 20100
ip -4 route add local 0.0.0.0/0 dev lo table 20100

ip -6 rule add priority 10100 fwmark 0x40000000/0x40000000 lookup 20100
ip -6 route add local ::/0 dev lo table 20100
```

The application applies this idempotently and removes its own rules when stopped. Interface hotplug recovery uses the last applied configuration. No continually accumulating anonymous UCI network sections are added.

## External requirements

- Backend replies must pass through the load balancer. When clients and backends share a subnet, direct L2 traffic can bypass that router.
- NAT/masquerading on the outgoing backend route must not rewrite the client IP. Check zone and NAT rules.
- Strict IPv4 reverse-path filtering can reject asymmetric routes. The application reports `rp_filter=1` on `all` or a configured return interface but does not change it globally. Choose appropriate interface settings for your routing; loose mode may suffice in some topologies.
- Other PBR/mwan3 rules must not interfere with the reserved mark bit or rule priority.
- Flow offloading can conflict with special policy-routing paths. Initially test without offloading and check counters and packet paths before reenabling it.
- Local clients with loopback source addresses and loopback backends are outside the scope of transparent mode. Use a separate ordinary forwarding service for these.
- UDP sessions do not have a TCP-style handshake. Passive failure detection therefore cannot prove that a UDP application is healthy.

## Diagnostics

```sh
ip -4 rule show
ip -6 rule show
ip -4 route show table 20100
ip -6 route show table 20100
nft list table inet nsm
nft list chain inet fw4 input
logread -e nginx-stream-manager
```

If available, use `tcpdump` on both the client and backend interfaces to confirm the source address and return path. A successful connection alone does not prove that source-IP preservation works.

## References

- Nginx stream proxy: https://nginx.org/en/docs/stream/ngx_stream_proxy_module.html
- Nginx stream upstream: https://nginx.org/en/docs/stream/ngx_stream_upstream_module.html
- Linux transparent sockets and policy routing: https://www.kernel.org/doc/html/latest/networking/tproxy.html
- Firewall4 include points: https://git.openwrt.org/project/firewall4/tree/root/usr/share/nftables.d/README
- LuCI API: https://openwrt.github.io/luci/jsapi/
- rpcd executable plugins: https://openwrt.org/docs/techref/rpcd
