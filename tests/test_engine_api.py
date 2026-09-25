"""Exercise real HTTP transport and the server-owned export boundary."""
import copy
import json
import io
import sys
import tempfile
import threading
import unittest
import zipfile
import ezdxf
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import local_cad_api as api
from cabinet_engine import evaluate_project, validate_project, RevisionConflict, RULE_FINGERPRINT
from questionnaire import initial_state, visible_groups, numeric_rule, prune_state

def complete():
    state = initial_state()
    for _ in range(20):
        for group in visible_groups(state):
            node = group['nodes'][0]
            key = f"{node['sheet']}:{node['order']}"
            kind = group['fieldType']
            if kind in ('list', 'radio_group'):
                state['selected'].setdefault(group['key'], node['order'])
            elif kind == 'input':
                rule = numeric_rule(node['inputRule'])
                state['inputs'][key] = str(rule['min']) if rule else 'M1'
            elif kind == 'check_box':
                state['checks'][key] = False
    return dict(id='one', tag='M1', state=state)

class EngineApiTests(unittest.TestCase):
    def test_main_scheme_choice_is_validated_and_preserved(self):
        motor = complete()
        motor['mainSchemeCode'] = 'im1-011'
        normalized = validate_project({'motors': [motor]})
        self.assertEqual(normalized[0]['mainSchemeCode'], 'im1-011')
        motor['mainSchemeCode'] = 'im1-999'
        self.assertFalse(evaluate_project({'motors': [motor]})['calculation']['canExport'])
        motor['mainSchemeCode'] = '../secret'
        with self.assertRaises(ValueError):
            validate_project({'motors': [motor]})

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, path, payload):
        request = Request(self.url + path, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        try:
            result = urlopen(request, timeout=15)
        except HTTPError as error:
            result = error
        with result:
            return result.status, json.load(result)

    def test_calculation_http_has_active_form_but_no_rule_database(self):
        status, result = self.post('/calculate', {'motors': [complete()]})
        self.assertEqual(status, 200)
        self.assertTrue(result['calculation']['canExport'])
        for group in result['forms']['one']:
            for node in group['nodes']:
                self.assertNotIn('binds', node)
                self.assertNotIn('engineKey', node)
        self.assertNotIn('mainSpecification', result)

    def test_bad_payloads_and_duplicate_ids_rejected(self):
        bad = complete()
        bad['state']['checks']['bad'] = 'true'
        for payload in [[], {}, {'motors': [bad]}, {'motors': [complete(), complete()]}, {'motors': [complete()] * 41}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.post('/calculate', payload)[0], 422)

    def test_old_revision_and_client_manifest_cannot_export(self):
        self.assertEqual(self.post('/generate', {'motors': [complete()], 'ruleFingerprint': 'old'})[0], 409)
        self.assertEqual(self.post('/generate', {'instances': [{'id': 'fake', 'code': 'im1-011'}], 'ruleFingerprint': RULE_FINGERPRINT})[0], 422)
        self.assertEqual(self.post('/generate', {'motors': [dict(id='one', tag='M1', state=initial_state())], 'ruleFingerprint': RULE_FINGERPRINT})[0], 422)

    def test_hidden_answers_are_removed_and_result_recomputed(self):
        motor = complete()
        motor['state']['inputs']['Ques 2:hidden'] = '30'
        result = evaluate_project({'motors': [motor]})
        self.assertNotIn('Ques 2:hidden', result['calculation']['blocks'][0]['state']['inputs'])
        self.assertTrue(result['calculation']['canExport'])

    def test_export_ignores_forged_instances_and_uses_server_calculation(self):
        motor = complete()
        expected = evaluate_project({'motors': [motor]})['calculation']['instances']
        with tempfile.TemporaryDirectory(prefix='l-api-test-') as directory:
            def assemble(command, **kwargs):
                target = Path(command[command.index('--output') + 1])
                manifest = json.loads(Path(command[command.index('--manifest') + 1]).read_text())
                self.assertEqual([i['id'] for i in manifest['instances']], [i['id'] for i in expected])
                self.assertNotIn('forged', [i['id'] for i in manifest['instances']])
                target.write_bytes(b'x' * 1024)
                return type('Result', (), dict(returncode=0, stdout='assembled'))()
            with patch.object(api, 'OUTPUT', Path(directory)), patch.object(api.subprocess, 'run', side_effect=assemble):
                status, result = self.post('/generate', {'motors': [motor], 'ruleFingerprint': RULE_FINGERPRINT, 'instances': [{'id': 'forged', 'code': 'im1-011'}]})
                self.assertEqual(status, 200, result)

    def test_unknown_block_export_rejected(self):
        self.assertEqual(self.post('/generate', {'motors': [complete()], 'blockId': 'other', 'ruleFingerprint': RULE_FINGERPRINT})[0], 422)

    def test_real_export_downloads_two_documents_with_repeated_blocks(self):
        first = complete()
        second = dict(copy.deepcopy(first), id='two', tag='M2')
        with tempfile.TemporaryDirectory(prefix='l-http-cad-') as directory, patch.object(api, 'OUTPUT', Path(directory)):
            status, result = self.post('/generate', {'motors': [first, second], 'ruleFingerprint': RULE_FINGERPRINT})
            self.assertEqual(status, 200, result)
            self.assertEqual(len(result['drawings']), 2)
            for drawing in result['drawings']:
                with urlopen(Request(self.url + drawing['downloadPath'], method='HEAD')) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn('attachment;', response.headers['Content-Disposition'])
                    self.assertGreater(int(response.headers['Content-Length']), 1024)
                with urlopen(self.url + drawing['downloadPath']) as response:
                    document = ezdxf.read(io.StringIO(response.read().decode('utf-8')))
                self.assertFalse(document.audit().has_errors)
                labels = [e.dxf.text for e in document.modelspace().query('TEXT') if e.dxf.text.startswith('DRAFT')]
                self.assertEqual(len(labels), 6 if drawing['kind'] == 'electrical' else 2)
            with urlopen(self.url + result['downloadPath']) as response:
                with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                    manifest_name = next(name for name in archive.namelist() if name.endswith('.json'))
                    manifest = json.loads(archive.read(manifest_name))
                    self.assertEqual({i['blockId'] for i in manifest['instances']}, {'one', 'two'})

if __name__ == '__main__':
    unittest.main()
