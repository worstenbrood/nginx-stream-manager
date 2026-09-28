# LuCI screenshots — 0.5.0

New captures of all seven main tabs and all six service tabs, taken on 28 September 2026 in the real, authenticated LuCI interface of a local OpenWrt 25.12.5 container. The ASUS router was not modified.

The traffic and timing graphs contain **synthetic demo data**, not router traffic. The three documentation addresses in the history are examples; the distribution does not prove actual backup traffic. Monitoring shows a real TCP probe to the local test web server on loopback. No certificates are configured in this fixture. Configuration was changed for that probe after the traffic history was generated.

## Overview

![Service overview](screenshots/overview.png)

## Configuration

The long configuration page is captured in two images.

![Global settings](screenshots/configuration-global.png)

![Pools, backends, services and routes](screenshots/configuration.png)

## Import Nginx

![Importing an existing Nginx configuration](screenshots/import.png)

## Configuration tools

![Snapshots, backup, duplication and SNI route test](screenshots/tools.png)

## Traffic

![Sent volume per upstream](screenshots/traffic.png)

![Mean connection times per upstream](screenshots/timing-0.5.png)

## Monitoring

![Active health check](screenshots/monitoring-0.5.png)

## Logs

The manager log is empty in this fresh test environment.

![Log viewer](screenshots/logs.png)

## Service: General

![General service settings](screenshots/service-general.png)

## Service: Network and access

![Network and access rules](screenshots/service-network.png)

## Service: Proxy and failover

![Proxy and failover settings](screenshots/service-proxy.png)

## Service: Logging

![Logging and traffic history](screenshots/service-logging.png)

## Service: Listener and limits

![Listener and connection limits](screenshots/service-limits.png)

## Service: TLS and routing

![TLS and pool routing](screenshots/tls-routing.png)

## Starting the local preview yourself

Use the OpenWrt test image from [BUILDING.md](BUILDING.md). Run the commands below from the repository in a POSIX shell; on Windows, change the bind mount to the absolute repository path.

```sh
docker run --rm -d --name nsm-ui-preview \
  --cap-add NET_ADMIN --cap-add SYS_PTRACE \
  -p 127.0.0.1:18766:80 -v "$PWD:/src:ro" \
  nsm-openwrt-userland:25.12.5 sh /src/tests/luci-preview.sh
docker logs nsm-ui-preview
# Wait for uhttpd to run, then populate the demo:
docker exec nsm-ui-preview python3 /src/tests/seed_screenshot_data.py
```

Open `http://127.0.0.1:18766/cgi-bin/luci/` and sign in with `root` / `nsm-local-preview`. These fixed credentials belong exclusively to this disposable fixture. Publish the container port only on host loopback as shown above. The first start installs uhttpd from the OpenWrt feed and requires network access. The seed is intended for a fresh container, not as a repeated traffic generator.

After capturing the images, close the browser and clean up the preview:

```sh
docker stop nsm-ui-preview
```
