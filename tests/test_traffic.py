import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_nsm import nsm, sample

NOW = 1800000000


def record(**fields):
    value = dict(service='proxy', time=str(NOW), protocol='TCP', upstream='192.0.2.10:9000', sent='120', received='240')
    value.update(fields)
    return (json.dumps(value) + '\n').encode()


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.run, self.log = root / 'run', root / 'log'
        self.run.mkdir(); self.log.mkdir()
        for name, value in [('RUN', self.run), ('LOG', self.log)]:
            p = patch.object(nsm, name, value); p.start(); self.addCleanup(p.stop)

    def write(self, value):
        with (self.log / 'traffic.log').open('ab') as stream:
            stream.write(value)

    def collect(self, now=NOW):
        with nsm.locked():
            return nsm.collect_traffic(now)

    def test_independent_opt_in_and_no_client_identifier(self):
        config = sample()
        self.assertNotIn('log_format traffic_', nsm.compile_config(config)['nginx'])
        config[-1]['traffic_history'] = '1'
        rendered = nsm.compile_config(config)['nginx']
        line = next(line for line in rendered.splitlines() if 'log_format traffic_' in line)
        self.assertIn('$upstream_bytes_sent', line)
        self.assertNotIn('$remote_addr', line)
        self.assertNotIn('access_log off;', rendered)
        config[-1]['access_log'] = '1'
        self.assertIn('.access.log nsm;', nsm.compile_config(config)['nginx'])

    def test_ipv6_retries_preserve_counter_alignment(self):
        rows = nsm.traffic_record(record(upstream='[2001:db8::1]:53, 192.0.2.2:53', sent='0, 12', received='0, 30'), NOW)
        self.assertEqual([r[2:] for r in rows], [(0, 0), (12, 30)])
        self.assertIn('[2001:db8::1]:53', rows[0][0])
        with self.assertRaises(nsm.Invalid):
            nsm.traffic_record(record(sent='1, 2'), NOW)

    def test_incremental_partial_lines_and_repeated_reads(self):
        line = record()
        self.write(line[:-3])
        self.assertEqual(self.collect()['series'], {})
        self.write(line[-3:])
        first = self.collect()
        self.assertEqual(list(first['series'].values())[0][str(NOW)], [120, 240, 1])
        self.assertEqual(self.collect()['series'], first['series'])

    def test_rename_rotation_and_late_old_worker_writes(self):
        self.write(record()); self.collect()
        os.rename(self.log / 'traffic.log', self.log / 'traffic.log.1')
        with (self.log / 'traffic.log.1').open('ab') as stream:
            stream.write(record(sent='20'))
        self.write(record(sent='30'))
        cell = self.collect()['series']['proxy|TCP|192.0.2.10:9000'][str(NOW)]
        self.assertEqual(cell, [170, 720, 3])
        self.assertEqual(self.collect()['series']['proxy|TCP|192.0.2.10:9000'][str(NOW)], cell)

    def test_invalid_and_oversized_records_do_not_block_following_data(self):
        self.write(b'x' * 10000 + b'\n' + b'{}\n' + record())
        data = self.collect()
        self.assertEqual(data['limited_at'], NOW)
        self.assertEqual(len(data['series']), 1)

    def test_retention_and_global_capacity(self):
        for i in range(12):
            self.write(record(time=str(NOW - i * 60), upstream='192.0.2.%d:80' % (i + 1)))
        with patch.object(nsm, 'TRAFFIC_CELLS', 5), patch.object(nsm, 'TRAFFIC_SERIES', 8):
            data = self.collect()
        self.assertLessEqual(sum(map(len, data['series'].values())), 5)
        self.assertEqual(data['limited_at'], NOW)
        self.assertEqual(self.collect(NOW + 86461)['series'], {})

    def test_read_budget_and_recovery_after_truncation(self):
        self.write(record() * 200)
        with patch.object(nsm, 'TRAFFIC_READ', 8192):
            data = self.collect()
        self.assertTrue(any(c['offset'] < c['size'] for c in data['cursors'].values()))
        (self.log / 'traffic.log').write_bytes(record(sent='5'))
        data = self.collect()
        self.assertEqual(data['limited_at'], NOW)
        self.assertEqual(self.collect()['series'], data['series'])

    def test_rpc_period_protocol_upstream_selection_and_bound(self):
        for i in range(12):
            self.write(record(upstream='192.0.2.%d:80' % (i + 1), sent=str(i + 1)))
        self.write(record(protocol='UDP', sent='7', upstream='192.0.2.1:80'))
        config = [{'.type': 'service', '.name': 'proxy'}]
        with patch.object(nsm, 'read_config', return_value=config), patch.object(nsm.time, 'time', return_value=NOW):
            result = nsm.rpc('traffic', {'service': 'proxy', 'hours': 24})
            self.assertEqual(len(result['series']), 8)
            self.assertEqual(result['omitted'], 4)
            self.assertEqual(len(result['series'][0]['points']), 96)
            self.assertLess(len(json.dumps(result)), 60000)
            result = nsm.rpc('traffic', {'service': 'proxy', 'protocol': 'UDP', 'upstream': '192.0.2.1:80'})
            self.assertEqual(result['series'][0]['sent'], 7)
            for args in ({'service': '../proxy'}, {'service': 'unknown'}, {'service': 'proxy', 'hours': 2}, {'service': 'proxy', 'hours': True}):
                with self.assertRaises(nsm.Invalid): nsm.rpc('traffic', args)

    def test_failed_state_write_can_be_retried_without_double_counting(self):
        self.write(record())
        with patch.object(nsm, 'atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.collect()
        data = self.collect()
        self.assertEqual(data['series']['proxy|TCP|192.0.2.10:9000'][str(NOW)], [120, 240, 1])

    def test_same_inode_rewrite_detected_even_when_file_grows(self):
        self.write(record())
        self.collect()
        (self.log / 'traffic.log').write_bytes(record(sent='999') * 2)
        data = self.collect()
        self.assertEqual(data['limited_at'], NOW)
        self.assertEqual(data['series']['proxy|TCP|192.0.2.10:9000'][str(NOW)], [2118, 720, 3])
        self.assertEqual(self.collect()['series'], data['series'])

    def test_unselected_upstream_and_old_record(self):
        self.write(record(upstream='pool_test', sent='-', received='-'))
        self.write(record(time=str(NOW - 90000)))
        self.assertEqual(self.collect()['series'], {})
