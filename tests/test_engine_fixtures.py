"""Frozen input/output scenarios for the server-owned engineering engine."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from cabinet_engine import calculate_cabinet
from questionnaire import BASE, prune_state, visible_groups

FIXTURES = json.loads((ROOT / 'tests' / 'fixtures' / 'engine-cases.json').read_text(encoding='utf-8'))


def concise(calculation, expected):
    instances = [f"{item['drawingKind']}:{item['code']}" for item in calculation['instances']]
    if expected['instanceCodes'] == ['count:20', 'unique:20']:
        instances = [f"count:{len(calculation['instances'])}", f"unique:{len({item['id'] for item in calculation['instances']})}"]
    blocks = []
    for block, contract in zip(calculation['blocks'], expected['blocks']):
        blocks.append({
            'id': block['id'],
            'mainCode': block.get('main', {}).get('diagram1'),
            'optionalCodes': [item['row'].get('diagram1') for item in block['optional']],
            'loadIndex': block['loadIndex'],
            'warnings': block['warnings'],
            'channels': [item['address'] for item in block['channels']],
        })
        if 'specification' in contract:
            blocks[-1]['specification'] = [
                [item['name'], item['quantity'], item['unit'], item['source']]
                for item in block['specification']
            ]
        if 'rules' in contract:
            blocks[-1]['rules'] = [
                [item['title'], item['source']] for item in block['decisionTrace']['rules']
            ]
        if 'specificationCount' in contract:
            blocks[-1]['specificationCount'] = len(block['specification'])
        if 'ruleCount' in contract:
            blocks[-1]['ruleCount'] = len(block['decisionTrace']['rules'])
    return {
        'canExport': calculation['canExport'], 'errors': calculation['errors'],
        'controllerFamily': calculation['controllerFamily'], 'io': calculation['io'],
        'instanceCodes': instances, 'blocks': blocks,
    }


class FrozenEngineFixturesTests(unittest.TestCase):
    def test_engine_cases(self):
        self.assertEqual(FIXTURES['format'], 1)
        for case in FIXTURES['cases']:
            with self.subTest(case=case['id']):
                actual = concise(calculate_cabinet(copy.deepcopy(case['motors'])), case['expected'])
                self.assertEqual(actual, case['expected'])

    def test_vfd_questionnaire_branches(self):
        by_id = {case['id']: case for case in FIXTURES['cases']}
        for case in FIXTURES['questionnaireCases']:
            with self.subTest(case=case['id']):
                state = by_id[case['sourceCase']]['motors'][0]['state']
                groups = visible_groups(state)
                self.assertEqual([group['nodes'][0]['order'] for group in groups], case['visibleOrders'])
                observed = {group['nodes'][0]['order']: group['nodes'][0].get('inputRule') for group in groups}
                for order, rule in case['inputRules'].items():
                    self.assertEqual(observed[order], rule)

    def test_hidden_answers_are_pruned_from_the_frozen_sensor_case(self):
        case = next(case for case in FIXTURES['cases'] if case['id'] == 'direct-motor-with-sensors')
        state = copy.deepcopy(case['motors'][0]['state'])
        state['checks']['Ques 1:14.1'] = False
        state['inputs']['Ques 2:made-up'] = '30'
        cleaned = prune_state(state)
        self.assertFalse(any(key.startswith('Ques 2:') for key in cleaned['inputs']))
        self.assertFalse(any(key.startswith('Ques 2:') for key in cleaned['selected']))

    def test_pt100_rtd_rows_use_one_two_three_inputs(self):
        for source_rows in ((5, 6, 7), (8, 9, 10)):
            actual = [next(row for row in BASE['diagrams2'] if row['sourceRow'] == source_row)['io']['AI-RTD2']
                      for source_row in source_rows]
            self.assertEqual(actual, [1, 2, 3])

    def test_unresolved_ptc_blocks_export_but_rtd_rows_remain_available(self):
        case = next(case for case in FIXTURES['cases'] if case['id'] == 'direct-motor-with-sensors')
        ptc = copy.deepcopy(case['motors'][0])
        ptc['state']['checks']['Ques 2:31.1'] = True
        result = calculate_cabinet([ptc])
        self.assertFalse(result['canExport'])
        self.assertTrue(any('PTC назначен на AI-RTD2' in error for error in result['errors']))

        three_wire = copy.deepcopy(case['motors'][0])
        key = 'Ques 2:Схема подключения Pt100_Датчик температуры обмотки Pt100:list'
        three_wire['state']['selected'][key] = '33.2'
        result = calculate_cabinet([three_wire])
        self.assertFalse(any('расход AI-RTD2 противоречив' in error for error in result['errors']))
        self.assertTrue(result['canExport'])


if __name__ == '__main__':
    unittest.main()
