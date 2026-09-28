# LuCI screenshots — 0.5.0 and 0.4.0

## New in 0.5.0: monitoring

Real LuCI interface in the local OpenWrt test container. The TCP probe checks the local test web server on loopback. The certificate is a temporary self-signed demo certificate with a short validity period; no router certificate was copied.

![Monitoring with health status and certificate warning](screenshots/monitoring-0.5.png)

## New in 0.5.0: connection times

The graph shows mean connection times per upstream with **synthetic demo data**, not measured ASUS router traffic. Missing recent samples appear as a gap, not as a measured time of zero.

![Connection-time graph with demo data](screenshots/timing-0.5.png)

## Earlier 0.4.0 views

These images were captured on 28 September 2026 in the real, authenticated LuCI interface of a local OpenWrt 25.12.5 container. They are not designs or recreated screens. The ASUS router was not modified.

## Traffic per upstream server

The three lines show **synthetic demo data** for three documentation addresses. The view shows sent volume per minute from completed sessions, with totals and share per upstream. This is not a live bandwidth measurement and the example distribution does not prove actual backup traffic.

![Traffic graph with three upstream servers](screenshots/traffic.png)

## Pools, backends and services

The middle backend is configured as a backup. Backup and administratively down status appear directly in the overview.

![Configuration with a backup server](screenshots/configuration.png)

## Service overview

The manager runs in the test container with one TCP listener on loopback.

![Running manager](screenshots/overview.png)

## TLS and SNI routing

The edit dialog shows TLS inspection without decryption and SNI routing. These choices were selected for the screenshot and then discarded; they were not applied to the demo service.

![TLS and SNI settings](screenshots/tls-routing.png)

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
