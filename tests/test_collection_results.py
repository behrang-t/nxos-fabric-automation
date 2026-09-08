"""Offline collection regression checks; no device or credentials required."""
import json
from pathlib import Path
import unittest

import yaml
from jinja2 import Environment, StrictUndefined
from filter_plugins.collection_results import collection_cli_result

ROOT = Path(__file__).resolve().parents[1]


class CollectionTests(unittest.TestCase):
    def test_stop_decision_and_error_precedence(self):
        cases = [
            ({'unreachable': True, 'stdout': ['partial output']}, False, 'error', False),
            ({'failed': True, 'stdout': ['partial output']}, False, 'error', False),
            ({'stdout': ['% Invalid input detected']}, False, 'error', False),
            ({'stdout': ['']}, False, 'error', False),
            ({'stdout': ['normal output']}, False, 'completed', False),
            ({'skipped': True}, True, 'error', True),
        ]
        for raw, stopped, status, stop in cases:
            with self.subTest(raw=raw, stopped=stopped):
                result = collection_cli_result(raw, stopped)
                self.assertEqual(result['execution_status'], status)
                self.assertEqual(result['stop_device'], stop)
                if 'stdout' in raw:
                    self.assertEqual(result['stdout'], raw['stdout'])

    def test_preflight_gate(self):
        failures = [{}, {'stdout': ['']}, {'stdout': ['% Invalid input detected']},
                    {'failed': True, 'msg': 'Operation timed out'},
                    {'unreachable': True, 'msg': 'connection unavailable'},
                    {'failed': True, 'stdout': ['partial output']}]
        for raw in failures:
            with self.subTest(raw=raw):
                gate = collection_cli_result(raw, preflight=True)
                self.assertTrue(gate['stop_device'])
                self.assertEqual(gate['execution_status'], 'error')
                if raw.get('msg'):
                    self.assertIn(raw['msg'], gate['msg'])
                for stage in ('snapshot', 'baseline', 'post_probe'):
                    unsent = collection_cli_result({'skipped': True}, gate['stop_device'],
                                                   preflight_message=gate['msg'])
                    self.assertIn(gate['msg'], unsent['msg'])
                    self.assertEqual(unsent['execution_status'], 'error')
        gate = collection_cli_result({'stdout': ['leaf1']}, preflight=True)
        self.assertFalse(gate['stop_device'])
        for raw in failures:
            outcome = collection_cli_result(raw, gate['stop_device'])
            self.assertFalse(outcome['stop_device'])
        self.assertEqual(collection_cli_result({'stdout': ['next command output']})[
            'execution_status'], 'completed')

    def test_full_output_round_trip(self):
        output = '\n'.join(f'feature{i} 1 enabled' for i in range(200))
        result = collection_cli_result({'stdout': [output]})
        self.assertFalse(result['failed'])
        self.assertEqual(json.loads(json.dumps(result))['stdout'][0], output)

    def test_cli_errors_and_context(self):
        for output in ['% Invalid input detected', 'header\n % ERROR: denied',
                       '% Incomplete command', '% Ambiguous command']:
            with self.subTest(output=output):
                result = collection_cli_result({'stdout': [output]})
                self.assertTrue(result['failed'])
                self.assertIn('CLI error:', result['msg'])
                self.assertEqual(result['stdout'][0], output)
        self.assertFalse(collection_cli_result({'stdout': ['Errors: 0\nload 25%']})['failed'])

    def test_absent_empty_and_transport_results(self):
        for result in [{}, {'stdout': []}, {'stdout': ['  \n']},
                       {'failed': True, 'msg': 'timeout'}, {'unreachable': True},
                       {'skipped': True}]:
            normalized = collection_cli_result(result)
            self.assertTrue(normalized['failed'])
            self.assertTrue(normalized['msg'])
        self.assertNotIn('unreachable', collection_cli_result({'stdout': ['']}))

    def test_inventory_roles_owned_by_groups(self):
        inventory = yaml.safe_load((ROOT / 'inventory/inventory.yml').read_text())
        groups = inventory['all']['children']['fabric_nodes']['children']['nxos']['children']
        for group in groups.values():
            self.assertIn('fabric_role', group['vars'])
            for host in group['hosts'].values():
                self.assertNotIn('fabric_role', host)

    def test_render_actual_payload_with_mixed_results(self):
        """Exercise the real final-report Jinja, including failed snapshot and unsent CLI."""
        playbook = yaml.safe_load((ROOT / 'playbooks/collect_vxlan_evpn_evidence.yml').read_text())
        task = next(t for t in playbook[-1]['tasks'] if 'collection_results_payload_json' in t.get('ansible.builtin.set_fact', {}))
        template = task['ansible.builtin.set_fact']['collection_results_payload_json']
        env = Environment(undefined=StrictUndefined)
        env.filters['to_json'] = json.dumps
        env.filters['combine'] = lambda a, b: dict(a, **b)
        plan = {'platform': {'roles': ['leaf'], 'commands': ['show feature', 'bad command', 'unsent command']}}
        records = []
        for cmd, result in [('show feature', {'stdout': ['line1\nline2']}),
                            ('bad command', {'stdout': ['% Invalid input detected']}),
                            ('unsent command', {'skipped': True})]:
            records.append(dict(collection_cli_result(result, cmd == 'unsent command'), item=[{'key': 'platform'}, cmd]))
        host = dict(fabric_role='leaf', ansible_host='192.0.2.1',
                    baseline_command_results={'results': records},
                    post_probe_command_results={'results': records},
                    running_config_snapshot_result=collection_cli_result({'unreachable': True}),
                    fabric_name='lab', fabric_site='site')
        probe = dict(id='p1', category='gateway', source='server1', destination='192.0.2.254')
        hostvars = {'leaf1': host, 'server1': {
            'probe_source_interface': 'eth1',
            'endpoint_probe_execution_plan': [dict(probe, measurement_command='ping example')],
            'traffic_probe_measurement_results': {'results': [dict(
                {'rc': 2, 'stdout': '', 'stderr': 'invalid option'}, item=probe)]},
        }}
        context = dict(collected_nxos_hosts=['leaf1'], reference_nxos_host='leaf1',
                       hostvars=hostvars, baseline_only_command_sets=plan,
                       pre_post_comparison_command_sets=plan, traffic_probes={'probes': [probe]},
                       run_id_effective='test', run_started_at='start', run_completed_at='end',
                       schema_version=1, raw_evidence_dir_effective='custom_raw',
                       structured_evidence_dir_effective='structured', report_dir_effective='reports')
        payload = json.loads(env.from_string(template).render(**context))
        self.assertEqual(payload['summary']['baseline'], {'planned': 3, 'completed': 1, 'errors': 2})
        self.assertEqual(payload['baseline']['devices']['leaf1']['commands'][0]['stdout'], 'line1\nline2')
        self.assertTrue(payload['baseline']['devices']['leaf1']['commands'][0]['raw_file'].startswith('custom_raw/'))
        self.assertEqual(payload['configuration_snapshots']['devices']['leaf1']['collection_status'], 'error')
        self.assertEqual(payload['traffic_probes'][0]['execution_status'], 'completed')
        self.assertEqual(payload['traffic_probes'][0]['return_code'], 2)
        self.assertEqual(payload['traffic_probes'][0]['stderr'], 'invalid option')
        report_context = dict(context, collection_results_payload=payload)
        report = env.from_string((ROOT / 'templates/evidence_report.md.j2').read_text()).render(**report_context)
        self.assertIn('CLI error:', report)
        self.assertIn('Command not sent:', report)
        # The schema freezes exactly the public top-level and command field names.
        schema = json.loads((ROOT / 'schemas/collection_results.schema.json').read_text())
        self.assertEqual(set(payload), set(schema['required']))
        for command in payload['baseline']['devices']['leaf1']['commands']:
            self.assertEqual(set(command), set(schema['$defs']['command']['required']))


if __name__ == '__main__':
    unittest.main()
