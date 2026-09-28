"""Shell lifecycle contract tests with stubbed backend and procd functions.

These are not live procd tests. rc.common calls start_service inside a wrapper
whose final successful close can mask the callback exit status.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InitContractTests(unittest.TestCase):
    def run_start(self, enabled='1', prepare='0', started='0'):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backend = root / 'backend'
            backend.write_text('''#!/bin/sh
printf '%s\n' "$1" >> "$NSM_EVENTS"
case "$1" in
  configured) echo "$NSM_ENABLED" ;;
  running) exit 1 ;;
  prepare) exit "$NSM_PREPARE" ;;
  started) exit "$NSM_STARTED" ;;
esac
''')
            backend.chmod(0o755)
            script = '''
. "$NSM_INIT"
PROG="$NSM_BACKEND"
procd_open_instance() { :; }
procd_set_param() { :; }
procd_close_instance() { :; }
procd_kill() { echo kill >> "$NSM_EVENTS"; }
start_service
# Simulate the successful rc.common/procd wrapper after start_service.
true
service_started
'''
            env = dict(os.environ, NSM_ENABLED=enabled, NSM_PREPARE=prepare, NSM_STARTED=started,
                       NSM_EVENTS=str(root / 'events'), NSM_BACKEND=str(backend),
                       NSM_INIT=str(ROOT / 'packages/nginx-stream-manager/files/nginx-stream-manager.init'))
            result = subprocess.run(['sh'], input=script, text=True, env=env, capture_output=True)
            return result.returncode, (root / 'events').read_text().splitlines()

    def test_prepare_failure_is_not_masked_and_stale_state_is_cleaned(self):
        status, events = self.run_start(prepare='1')
        self.assertEqual(status, 1)
        self.assertNotIn('started', events)
        self.assertEqual(events[-1], 'stopped')

    def test_worker_failure_stops_instances_before_cleanup(self):
        status, events = self.run_start(started='1')
        self.assertEqual(status, 1)
        self.assertEqual(events[-3:], ['started', 'kill', 'stopped'])

    def test_disabled_start_cleans_previously_registered_state(self):
        status, events = self.run_start(enabled='0')
        self.assertEqual(status, 0)
        self.assertEqual(events, ['configured', 'stopped'])


if __name__ == '__main__': unittest.main()
