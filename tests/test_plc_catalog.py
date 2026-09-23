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

    def test_distinct_inputs_share_group_common_without_reusing_pin(self):
        result = allocate_io([demand('one', {'DI24-PNP': 1}), demand('two', {'DI24-PNP': 1})])
        self.assertNotIn('error', result)
        first, second = [item['channels'][0] for item in result['allocations']]
        self.assertEqual((first['address'], second['address']), ('01', '02'))
        self.assertEqual({first['commonKey'], second['commonKey']}, {'#GND1'})
        self.assertNotEqual(first['address'], second['address'])

    def test_shared_group_capacity_blocks_export_even_when_code_limit_allows(self):
        # Six RTD pins occupy group 3. Only six group-2 NPN pins remain,
        # although the headline NPN count in the catalog is twelve.
        result = allocate_io([demand('rtd', {'AI-RTD2': 6}), demand('npn', {'DI24-NPN': 7})])
        self.assertIn('error', result)
        self.assertIn('Недостаточно', result['error'])

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


if __name__ == '__main__':
    unittest.main()
