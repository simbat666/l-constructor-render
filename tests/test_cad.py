import importlib.util
import copy
from pathlib import Path
import tempfile
import unittest
import json
import hashlib
import zipfile
import ezdxf
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

api = module('local_cad_api')
assembler = module('assemble_dxf_package')

class CadTests(unittest.TestCase):
    def test_im1_011_source_and_field_contract_match(self):
        source = ROOT / 'data/dxf-sources/im1-011.dxf'
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        contract = json.loads((ROOT / 'data/cad-template-contracts/im1-011.json').read_text(encoding='utf-8'))
        manifest = json.loads((ROOT / 'data/dxf-sources/conversion-manifest.json').read_text(encoding='utf-8'))
        record = next(item for item in manifest['converted'] if item['code'] == 'im1-011')
        self.assertEqual(contract['sourceSha256'], digest)
        self.assertEqual(record['output']['sha256'], digest)
        self.assertEqual(record['output']['bytes'], source.stat().st_size)
        document = ezdxf.readfile(source)
        self.assertFalse(document.audit().has_errors)
        for field in contract['fields']:
            entity = document.entitydb.get(field['handle'])
            self.assertIsNotNone(entity)
            self.assertEqual(entity.dxftype(), 'MTEXT')
            self.assertEqual(entity.dxf.layer, field['layer'])
            self.assertEqual(assembler.mtext_plain(entity), field['text'])

    def test_device_designations_are_scoped_to_the_full_cabinet(self):
        selected = assembler.load_selected(
            assembler.DEFAULT_LIBRARY,
            assembler.DEFAULT_DXF_SOURCES,
            ['im1-014', 'im1-014'],
        )
        with tempfile.TemporaryDirectory(prefix='l-designations-test-') as directory:
            output = Path(directory) / 'result.dxf'
            assembler.assemble(
                assembler.split_pages(assembler.load_and_validate(selected)),
                output,
            )
            document = ezdxf.readfile(output)
            actual = {
                (assembler.source_layer_name(entity.dxf.layer), assembler.mtext_plain(entity))
                for entity in document.modelspace()
                if entity.dxftype() == 'MTEXT'
                and assembler.source_layer_name(entity.dxf.layer) in {'QF', 'KM'}
            }
            self.assertTrue({
                ('QF', '1'), ('QF', '1.1'), ('QF', '2'), ('QF', '2.1'),
                ('KM', '1'), ('KM', '1.1'), ('KM', '2'), ('KM', '2.1'),
            }.issubset(actual))
            all_markers = {
                (assembler.source_layer_name(entity.dxf.layer), assembler.mtext_plain(entity))
                for entity in document.modelspace()
                if entity.dxftype() == 'MTEXT'
                and assembler.PLACEHOLDER.fullmatch(assembler.mtext_plain(entity))
            }
            # The identical marker on a terminal or header layer is not a
            # device number and must survive repeated device templates intact.
            self.assertIn(('XT1', '#1'), all_markers)
            self.assertIn(('XT7', '#2'), all_markers)
            self.assertIn(('Headers', '#1'), all_markers)

    def test_device_renumbering_trace_keeps_source_layer_and_handle(self):
        selected = assembler.load_selected(
            assembler.DEFAULT_LIBRARY,
            assembler.DEFAULT_DXF_SOURCES,
            ['im1-054', 'im1-054'],
        )
        with tempfile.TemporaryDirectory(prefix='l-designation-trace-test-') as directory:
            trace = []
            assembler.assemble(
                assembler.split_pages(assembler.load_and_validate(selected)),
                Path(directory) / 'result.dxf',
                parameter_trace=trace,
                drawing_kind='electrical',
            )
        self.assertTrue(trace)
        self.assertEqual({item['layer'] for item in trace}, {'QF', 'KL'})
        self.assertTrue(all(item['placeholderHandle'] for item in trace))
        self.assertTrue(all(item['drawingKind'] == 'electrical' for item in trace))
        self.assertIn(('#1', '2'), {(item['before'], item['after']) for item in trace})

    def test_wrong_document_kind_rejected(self):
        with self.assertRaises(ValueError):
            api.validate_instances({'instances': [{'id': 'motor', 'code': 'im1-011', 'drawingKind': 'external'}]})

    def test_im1_011_uses_verified_plc_and_terminal_fields(self):
        channels = [
            {'family': 'DI24-NPN', 'address': '07', 'commonKey': '#GND1', 'commonDesignation': 'GND1', 'terminals': ['07']},
            {'family': 'DOR-NO', 'address': 'Q1-1', 'terminals': ['Q1-1', 'Q1-2']},
        ]
        instances = [
            {'id': f'm{index}', 'tag': f'M{index}', 'code': 'im1-011', 'drawingKind': 'electrical',
             'channels': channels, 'cadHeadRules': [
                 {'sourceRow': 2, 'code': 'im1-011', 'keyDiagram': 'Imd-3', 'placeholder': '#Process Device Type1', 'value': 'Насос'},
                 {'sourceRow': 3, 'code': 'im1-011', 'keyDiagram': 'Imd-1', 'placeholder': '#Marking2', 'value': f'Н{index}'},
                 {'sourceRow': 4, 'code': 'im1-011', 'keyDiagram': 'Imd-2', 'placeholder': '#Device tag', 'value': f'M{index}'},
             ]}
            for index in (1, 2)
        ]
        selected = assembler.load_selected(assembler.DEFAULT_LIBRARY, assembler.DEFAULT_DXF_SOURCES, ['im1-011'] * 2)
        with tempfile.TemporaryDirectory(prefix='l-plc-fields-test-') as directory:
            trace = []
            output = Path(directory) / 'result.dxf'
            assembler.assemble(assembler.split_pages(assembler.load_and_validate(selected)), output,
                               instances=instances, parameter_trace=trace, drawing_kind='electrical')
            document = ezdxf.readfile(output)
            self.assertFalse(document.audit().has_errors)
        fields = {(item['instanceId'], item['fieldId']): item['after'] for item in trace}
        self.assertEqual(fields['m1', 'terminal.xt1.1'], '1')
        self.assertEqual(fields['m1', 'terminal.xt1.2'], '2')
        self.assertEqual(fields['m2', 'terminal.xt1.1'], '3')
        self.assertEqual(fields['m2', 'terminal.xt1.2'], '4')
        self.assertEqual(fields['m2', 'device.qf.contact'], 'QF2.1')
        self.assertEqual(fields['m1', 'plc.di'], '07')
        self.assertEqual(fields['m1', 'plc.do.1'], 'Q1-1')
        self.assertEqual(fields['m1', 'plc.do.2'], 'Q1-2')
        self.assertEqual(fields['m1', 'plc.common'], 'GND1')
        self.assertEqual(fields['m1', 'header.deviceTag'], 'M1')
        self.assertEqual(fields['m1', 'header.processDeviceType'], 'Насос')
        self.assertEqual(fields['m1', 'header.marking'], 'Н1')
        self.assertFalse(any(item['status'] == 'unresolved' for item in trace))
        self.assertEqual(next(item['ruleSource'] for item in trace if item['fieldId'] == 'header.marking'), 'diagram head:3')
        bad_head = copy.deepcopy(instances[0])
        bad_head['cadHeadRules'][1]['placeholder'] = '#Other'
        source = ezdxf.readfile(assembler.DEFAULT_DXF_SOURCES / 'im1-011.dxf')
        source_hash = assembler.load_and_validate(selected)[0][4]
        with self.assertRaisesRegex(ValueError, 'diagram head'):
            assembler.apply_template_contract('im1-011', source.modelspace(), source_hash, bad_head, {})
        with self.assertRaisesRegex(ValueError, 'версия DXF'):
            assembler.apply_template_contract('im1-011', source.modelspace(), '0' * 64, instances[0], {})
        with self.assertRaisesRegex(ValueError, 'DI24-NPN'):
            assembler.apply_template_contract('im1-011',
                ezdxf.readfile(assembler.DEFAULT_DXF_SOURCES / 'im1-011.dxf').modelspace(),
                assembler.load_and_validate(selected)[0][4], {'tag': 'M1', 'channels': []}, {})
        module_instance = copy.deepcopy(instances[0])
        module_instance['channels'][0]['deviceRef'] = 'M1'
        with self.assertRaisesRegex(ValueError, 'маркировки вывода модуля'):
            assembler.apply_template_contract('im1-011',
                ezdxf.readfile(assembler.DEFAULT_DXF_SOURCES / 'im1-011.dxf').modelspace(),
                source_hash, module_instance, {})

    def test_separate_document_sets_and_multipage_frames(self):
        instances = api.validate_instances({'instances': [{'id': f'm{index}-{code}', 'tag': f'M{index}', 'code': code} for index in range(8) for code in ['im1-014', 'im2-1']]})
        with tempfile.TemporaryDirectory(prefix='l-sets-test-') as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'instances': instances}), encoding='utf-8')
            output = root / 'result.zip'
            assembler.build_bundle(instances, output, manifest)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {'result-electrical.dxf', 'result-external.dxf', 'manifest.json'})
                exported_manifest = json.loads(archive.read('manifest.json'))
                parameterization = exported_manifest['cadParameterization']
                self.assertEqual(parameterization['contractVersion'], 1)
                self.assertTrue(parameterization['fields'])
                self.assertTrue(all(item['layer'] in {'QF', 'KM', 'KL'} for item in parameterization['fields']))
                self.assertTrue(all(item['placeholderHandle'] for item in parameterization['fields']))
            for kind, forbidden in [('electrical', 'im2-1'), ('external', 'im1-014')]:
                document = ezdxf.readfile(root / f'result-{kind}.dxf')
                self.assertFalse(document.audit().has_errors)
                labels = [entity.dxf.text for entity in document.modelspace().query('TEXT') if entity.dxf.text.startswith('DRAFT')]
                self.assertEqual(len(labels), 8)
                self.assertFalse(any(forbidden in label for label in labels))
                frame_count = len([entity for entity in document.modelspace().query('INSERT') if '2-я страница' in entity.dxf.name])
                self.assertGreater(frame_count, 1)

    def test_distinct_instances_may_share_template(self):
        data = api.validate_instances({'instances': [{'id': 'm1', 'code': 'im1-011'}, {'id': 'm2', 'code': 'im1-011'}]})
        self.assertEqual(len(data), 2)

    def test_reject_bad_requests(self):
        for payload in [[], {}, {'instances': []}, {'instances': [{'id': 'x', 'code': '../secret'}]}, {'instances': [{'id': 'x', 'code': 'im1-011'}, {'id': 'x', 'code': 'im1-011'}]}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                api.validate_instances(payload)

    def test_repeated_templates_round_trip(self):
        codes = ['im1-014', 'im2-1', 'im1-014', 'im2-1']
        selected = assembler.load_selected(assembler.DEFAULT_LIBRARY, assembler.DEFAULT_DXF_SOURCES, codes)
        with tempfile.TemporaryDirectory(prefix='l-cad-test-') as directory:
            output = Path(directory) / 'result.dxf'
            assembler.assemble(assembler.split_pages(assembler.load_and_validate(selected)), output)
            document = ezdxf.readfile(output)
            self.assertFalse(document.audit().has_errors)
            self.assertEqual(document.header['$INSUNITS'], 4)
            labels = [item.dxf.text for item in document.modelspace().query('TEXT') if item.dxf.text.startswith('DRAFT')]
            self.assertEqual(len(labels), 4)

if __name__ == '__main__':
    unittest.main()
