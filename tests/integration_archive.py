"""Exercise persistent history on a real Docker tmpfs mount, not a mocked path."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, '/src/packages/nginx-stream-manager/files')
import nsm

if not Path('/.dockerenv').exists() or not Path('/mnt/nsm-test').is_mount():
    raise SystemExit('Run in Docker with --tmpfs /mnt/nsm-test:rw,size=16m,mode=700')

with tempfile.TemporaryDirectory() as td:
    nsm.RUN = Path(td)/'run'; nsm.LOG = Path(td)/'log'
    nsm.RUN.mkdir(); nsm.LOG.mkdir()
    nsm.atomic(nsm.RUN/'state.json', json.dumps({'observability': {'history_path': '/mnt/nsm-test/history', 'history_days': '7'}, 'traffic_services': ['demo']}))
    now = int(time.time()) // 3600 * 3600
    record = dict(service='demo', protocol='TCP', upstream='127.0.0.1:9000', sent='123', received='456', time=str(now), connect='0.025', session='1.500', status='200')
    (nsm.LOG/'traffic.log').write_text(json.dumps(record)+'\n')
    with nsm.locked(): original = nsm.collect_traffic(now)
    archive = Path('/mnt/nsm-test/history/history.json')
    assert archive.exists() and archive.stat().st_mode & 0o777 == 0o600
    (nsm.RUN/'traffic.json').unlink()
    with nsm.locked(): restored = nsm.collect_traffic(now+1)
    assert restored['series'] == original['series'], 'Checkpoint restore must not double-count existing logs'
    with nsm.locked(): nsm.collect_traffic(now+2*86400)
    with patch.object(nsm, 'read_config', return_value=[{'.type': 'service', '.name': 'demo'}]), patch.object(nsm.time, 'time', return_value=now+2*86400):
        result = nsm.traffic({'service': 'demo', 'hours': 168})
    assert result['series'][0]['sent'] == 123 and result['series'][0]['connect_ms'] == 25, result
    nsm.atomic(nsm.RUN/'state.json', json.dumps({'observability': {'history_path': '/mnt/nsm-absent/history'}}))
    with nsm.locked(): nsm.collect_traffic(now+2*86400+1)
    assert 'not mounted' in (nsm.RUN/'archive-error').read_text()
    assert not Path('/mnt/nsm-absent').exists(), 'Absent mount must not cause fallback directories/files'
print('Real mounted history checkpoint, no duplicate restore, seven-day graph and missing-mount fallback: OK')
