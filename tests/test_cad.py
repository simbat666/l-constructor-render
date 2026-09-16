import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
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
    def test_device_designations_are_scoped_to_the_full_cabinet(self):
        selected = assembler.load_selected(assembler.DEFAULT_LIBRARY, assembler.DEFAULT_DXF_SOURCES, ['im1-011', 'im1-011'])
        with tempfile.TemporaryDirectory(prefix='l-designations-test-') as directory:
            output = Path(directory) / 'result.dxf'
            assembler.assemble(assembler.split_pages(assembler.load_and_validate(selected)), output)
            document = ezdxf.readfile(output)
            fields = assembler.designation_fields(document.modelspace(), assembler.load_field_contract())
            actual = {(item['prefix'], assembler.mtext_plain(item['placeholder'])) for item in fields}
            self.assertTrue({
                ('QF', '#1'), ('QF', '#1.1'), ('QF', '#2'), ('QF', '#2.1'),
                ('KM', '#1'), ('KM', '#1.1'), ('KM', '#2'), ('KM', '#2.1'),
            }.issubset(actual))
            all_markers = {
                (assembler.source_layer_name(entity.dxf.layer), assembler.mtext_plain(entity))
                for entity in document.modelspace()
                if entity.dxftype() == 'MTEXT' and assembler.PLACEHOLDER.fullmatch(assembler.mtext_plain(entity))
            }
            self.assertIn(('XT1', '#1'), all_markers)
            self.assertIn(('XT7', '#2'), all_markers)
            self.assertIn(('Headers', '#1'), all_markers)

    def test_device_renumbering_trace_keeps_source_layer_and_handle(self):
        selected = assembler.load_selected(assembler.DEFAULT_LIBRARY, assembler.DEFAULT_DXF_SOURCES, ['im1-054', 'im1-054'])
        with tempfile.TemporaryDirectory(prefix='l-designation-trace-test-') as directory:
            trace = []
            assembler.assemble(assembler.split_pages(assembler.load_and_validate(selected)), Path(directory) / 'result.dxf', parameter_trace=trace, drawing_kind='electrical')
        self.assertTrue(trace)
        self.assertEqual({item['layer'] for item in trace}, {'QF', 'KL'})
        self.assertTrue(all(item['placeholderHandle'] for item in trace))
        self.assertTrue(all(item['drawingKind'] == 'electrical' for item in trace))
        self.assertIn(('#1', '#2'), {(item['before'], item['after']) for item in trace})

    def test_wrong_document_kind_rejected(self):
        with self.assertRaises(ValueError):
            api.validate_instances({'instances': [{'id': 'motor', 'code': 'im1-011', 'drawingKind': 'external'}]})

    def test_separate_document_sets_and_multipage_frames(self):
        instances = api.validate_instances({'instances': [{'id': f'm{index}-{code}', 'tag': f'M{index}', 'code': code} for index in range(8) for code in ['im1-011', 'im2-1']]})
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
            for kind, forbidden in [('electrical', 'im2-1'), ('external', 'im1-011')]:
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
        codes = ['im1-011', 'im2-1', 'im1-011', 'im2-1']
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
