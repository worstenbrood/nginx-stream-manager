"""Synthetic completed-session history for screenshots; disposable UI container only."""
import json
import math
from pathlib import Path
import subprocess
import time

if not Path('/.dockerenv').exists() or not Path('/src/tests/luci-preview.sh').exists():
    raise SystemExit('Run only inside the disposable LuCI preview container')

subprocess.run(['uci', 'revert', 'nginx_stream_manager'], check=True)
subprocess.run(['uci', 'set', 'nginx_stream_manager.application_tcp.name=DEMO - Application TCP'], check=True)
subprocess.run(['uci', 'set', 'nginx_stream_manager.node_b.backup=1'], check=True)
for assignment in ('main.enabled=1', 'main.workers=1', 'application_tcp.listen_address=127.0.0.1',
                   'application_tcp.allow_source=127.0.0.0/8', 'application_tcp.traffic_history=1',
                   'node_c=backend', 'node_c.pool=application', 'node_c.address=192.0.2.12', 'node_c.port=9000'):
    subprocess.run(['uci', 'set', 'nginx_stream_manager.' + assignment], check=True)
subprocess.run(['uci', 'commit', 'nginx_stream_manager'], check=True)
subprocess.run(['/etc/init.d/nginx-stream-manager', 'start'], check=True)
subprocess.run(['nft', 'insert', 'rule', 'inet', 'fw4', 'input', 'tcp', 'dport', '80', 'accept'], check=True)
now = int(time.time()) // 60 * 60
log = Path('/var/log/nginx-stream-manager')
log.mkdir(parents=True, exist_ok=True)
with (log / 'traffic.log').open('w') as stream:
    for minute in range(60):
        for index, peer in enumerate(('192.0.2.10:9000', '192.0.2.11:9000', '192.0.2.12:9000')):
            volume = int((1 + .55 * math.sin(minute / 6 + index * 1.7)) * (index + 1) * 75000)
            stream.write(json.dumps({'service': 'application_tcp', 'time': str(now - (59 - minute) * 60),
                                     'protocol': 'TCP', 'upstream': peer, 'sent': str(volume),
                                     'received': str(volume * (index + 2)),
                                     'connect': '%.3f' % (.005 + index*.01 + .003*math.sin(minute/5)),
                                     'session': '%.3f' % (1.5 + index + .5*math.sin(minute/4)),
                                     'status': '200'}) + '\n')
print('Synthetic demo history written. Demo manager listens only on container loopback.')
