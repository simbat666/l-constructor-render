"""Regression checks for the supplied ZENTEC M245 pin contracts."""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from io_allocator import CATALOG, allocate_io  # noqa: E402


def demand(identity, io, source_row=2):
    return {'id': identity, 'candidates': [{'sourceRow': source_row, 'io': io}]}


class PlcCatalogTests(unittest.TestCase):
    def test_snapshot_tracks_both_supplied_workbooks(self):
        for key in ('composition', 'marking'):
            source = CATALOG['sources'][key]
            path = ROOT / 'data' / 'rules-source' / source['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source['sha256'])
        self.assertEqual(len({row['terminals'][0] for row in CATALOG['pinOptions']}), 35)
        self.assertEqual(len({row['terminals'][0] for row in CATALOG['modulePinOptions']}), 21)
        self.assertEqual(CATALOG['controller']['maxModules'], 5)

    def test_distinct_inputs_share_group_common_without_reusing_pin(self):
        result = allocate_io([demand('one', {'DI24-PNP': 1}), demand('two', {'DI24-PNP': 1})])
        self.assertNotIn('error', result)
        first, second = [item['channels'][0] for item in result['allocations']]
        self.assertEqual((first['address'], second['address']), ('01', '02'))
        self.assertEqual({first['commonKey'], second['commonKey']}, {'#GND1'})
        self.assertNotEqual(first['address'], second['address'])

    def test_shared_group_capacity_selects_one_module(self):
        # Six base RTD pins occupy group 3; the seventh NPN needs the module.
        result = allocate_io([demand('rtd', {'AI-RTD2': 6}), demand('npn', {'DI24-NPN': 7})])
        self.assertNotIn('error', result)
        self.assertEqual(len(result['modules']), 1)
        channels = [channel for block in result['allocations'] for channel in block['channels']]
        self.assertEqual(len({(c['deviceRef'], c['address']) for c in channels}), len(channels))

    def test_compatible_variant_uses_another_group_when_one_is_full(self):
        result = allocate_io([
            demand('rtd', {'AI-RTD2': 6}),
            {'id': 'sensor', 'candidates': [
                {'sourceRow': 2, 'io': {'DI24-NPN': 7}},
                {'sourceRow': 3, 'io': {'DI24-PNP': 1}},
            ]},
        ])
        self.assertNotIn('error', result)
        self.assertEqual(result['allocations'][1]['scheme']['sourceRow'], 3)
        self.assertEqual(result['allocations'][1]['channels'][0]['commonKey'], '#GND1')

    def test_dedicated_output_is_used_before_universal_output(self):
        result = allocate_io([demand('output', {'DOT-PNP': 1})])
        self.assertEqual(result['allocations'][0]['channels'][0]['address'], 'T1')
        self.assertEqual(result['modules'], [])
        more = allocate_io([demand('outputs', {'DOT-PNP': 3})])
        addresses = {c['address'] for c in more['allocations'][0]['channels']}
        self.assertEqual({'T1', 'T2'} & addresses, {'T1', 'T2'})
        self.assertTrue(any(address.startswith('U') for address in addresses))

    def test_module_address_is_local_to_each_device(self):
        result = allocate_io([demand('relay', {'DOR-NO': 6})])
        self.assertEqual(len(result['modules']), 1)
        channels = result['allocations'][0]['channels']
        self.assertEqual(channels[0]['address'], channels[5]['address'])
        self.assertEqual((channels[0]['deviceRef'], channels[5]['deviceRef']), ('PLC', 'M1'))
        self.assertEqual(channels[5]['source'], 'Клеммы модуля тест:41')

    def test_common_designation_is_scoped_to_device(self):
        result = allocate_io([demand('inputs', {'DI24-PNP': 13})])
        channels = result['allocations'][0]['channels']
        self.assertIn('PLC:GND1', {c['commonRef'] for c in channels})
        self.assertIn('M1:GND1', {c['commonRef'] for c in channels})

    def test_two_modules_are_selected_only_when_one_cannot_fit(self):
        result = allocate_io([demand('mixed', {'DI24-HSC-NPN': 7, 'DI24-NPN': 13})])
        self.assertEqual(len(result['modules']), 2)
        channels = result['allocations'][0]['channels']
        self.assertEqual(len({(c['deviceRef'], c['address']) for c in channels}), len(channels))

    def test_scheme_variant_prefers_fewer_modules(self):
        result = allocate_io([{'id': 'variant', 'candidates': [
            {'sourceRow': 2, 'io': {'DOR-NO': 6}},
            {'sourceRow': 3, 'io': {'DOT-PNP': 6}},
        ]}])
        self.assertEqual(result['allocations'][0]['scheme']['sourceRow'], 3)
        self.assertEqual(result['modules'], [])

    def test_five_module_limit_is_enforced(self):
        self.assertEqual(len(allocate_io([demand('relays', {'DOR-NO': 30})])['modules']), 5)
        result = allocate_io([demand('relays', {'DOR-NO': 31})])
        self.assertIn('Недостаточно', result['error'])


if __name__ == '__main__':
    unittest.main()
