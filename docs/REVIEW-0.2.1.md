# Technical review 0.2.1

Source review on 28 September 2026. No AGENTS.md files were found in the project or its parent directories. No real router was accessed or modified.

## Findings and fixes

| Component | Specific finding | Change / verification |
|---|---|---|
| Installer and packages | Forcing nginx-full could conflict with an existing provider; manual scripts overwrote package files; hooks ignored errors and disabled boot start during upgrades. | Preserve the existing provider; detect package installations and refuse to mix methods; use `nginx` as the provider dependency; obtain kmod-nf-socket transitively; propagate error codes; preserve upgrade boot preference in the new hook; reload in postrm; declare package architecture. Static checks only, no SDK build. |
| procd | Preparation was presented as a successful start; network cleanup preceded process termination; rc.common can mask an error from start_service. | service_started checks preparation and real workers; service_stopped waits for process termination; repeated start does not change an active configuration. Source order compared with rc.common; real procd acceptance still missing. |
| rpcd / ACL | Arguments could be arrays/incorrect types; service actions could exceed the RPC timeout; no uniform total limit. | Allowlist and type checking; 8192 input characters, 60,000 serialized output bytes; detached service job with its own lock and status. Signatures and ACL remain limited to NSM; read-only permissions do not grant import/control. No live ubus test. |
| LuCI | Pool choices were built once during rendering and could become stale after Save. | Rebuild choices during option.load; show action progress and errors in Overview. Import already called uci.unload; confirmed against the LuCI API. JavaScript syntax checked; browser behavior still needs target testing. |
| UCI / import | A private `-P` directory does not remove the default delta path, so parsed configuration could differ from token bytes. Backup converted line endings. Invalid duplicate/context directives could be silently converted. | Unique snapshot package in a private confdir; same bytes for token and parser; byte-exact backup; stricter parser and regressions for inheritance, duplicate directives, conditional logs and concurrent edits. Real UCI round-trip with five fixture services passes. |
| Listeners | SO_REUSEPORT allows external processes to share a port, so a successful Nginx start can hide a migration conflict. | /proc network socket checks exclude the application's master/worker inodes and reject external overlap. Real loopback/wildcard TCP/UDP tests, IPv6 binding, reload and an occupied-port test. No guarantee against a bind race after the check. |
| nftables | The temporary preflight table closed two blocks without the required separator; mocks did not catch this. | Generated test table uses correct newlines. Real nft check and apply included in Linux testing. |
| Routing / firewall | Route replacement preceded priority checking. Substring matching could accept another lookup table. Firewall rollback only rewrote a snippet. | Check exact selectors first; add instead of replace; narrow delete selectors; firewall recovery actually checks and reloads. Tests preserve foreign routes and check the recovery path. |
| Partial failure / reload | Recovery HUP was not confirmed; information about new routes could be lost after partial failure. | Apply journal records involved families; block new applies after incomplete recovery; check workers after recovery HUP; preserve state on cleanup errors. Worker observation remains a heuristic. |
| Interface events | A recovery request was lost when the application lock was busy. | Marker triggers a maintenance retry approximately every 30 seconds. Normal fw4 reload preserves inet nsm; a full ruleset flush requires explicit recovery. |

## Deliberate limitations

- No complete rewrite, modification of user UCI during upgrade, automatic migration of old TPROXY rules or sysctls.
- UCI file replacement and network apply are not a single transaction. An external editor/rpcd session does not share the application lock. The final import check leaves a small race window before `os.replace`.
- A new stable worker PID does not prove the loaded configuration, all listeners or backend health. A concurrent respawn can distort reload observation.
- Cleanup distinguishes selectors, not who originally added an identical rule. The reserved table/priority/mark must not be shared. Concurrent external network changes remain outside the transaction.
- Installing dependencies may run their own postinst/service actions. Manual copying is not an atomic package upgrade. A failure midway requires recovery or reinstallation while the manager remains stopped.
- The prerm of an **already installed 0.2.0** can still disable boot start. The new hook cannot retroactively fix that old code without knowing the previous boot preference.
- No OpenWrt SDK, real procd/rpcd/firewall4/LuCI session or target kernel tested. Linux container results do not replace those checks.

## Primary sources consulted

- [OpenWrt rc.common: start, stop and completion hooks](https://github.com/openwrt/openwrt/blob/main/package/base-files/files/etc/rc.common)
- [OpenWrt package lifecycle](https://github.com/openwrt/openwrt/blob/main/package/base-files/files/lib/functions.sh)
- [Nginx package and module paths](https://github.com/openwrt/packages/blob/master/net/nginx/Makefile)
- [OpenWrt nft-socket dependencies](https://github.com/openwrt/openwrt/blob/main/package/kernel/linux/modules/netfilter.mk)
- [UCI delta-path behavior](https://github.com/openwrt/uci/blob/master/delta.c)
- [rpcd executable plugins and signatures](https://github.com/openwrt/rpcd/blob/master/plugin.c)
- [LuCI UCI cache](https://openwrt.github.io/luci/jsapi/LuCI.uci.html)
- [Nginx stream listen, bind, ipv6only and reuseport](https://nginx.org/en/docs/stream/ngx_stream_core_module.html#listen)
- [firewall4 reload versus flush](https://github.com/openwrt/firewall4/blob/master/root/sbin/fw4)

Sources support the review but do not establish compatibility with an unspecified OpenWrt release.
