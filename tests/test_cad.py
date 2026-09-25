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

def im1_014_channels():
    return [
        {'family': 'DI24-PNP', 'address': '01', 'terminals': ['01'],
         'deviceRef': 'PLC', 'commonDesignation': 'GND1'},
        {'family': 'DOT-PNP', 'address': 'T1', 'terminals': ['T1'],
         'deviceRef': 'PLC', 'commonDesignation': 'GND3'},
    ]

class CadTests(unittest.TestCase):
    def test_new_motor_templates_keep_the_old_sheet_anchor(self):
        profile = json.loads(assembler.PROFILES.read_text(encoding='utf-8'))['electrical']
        codes = ['im1-011', 'im1-012', 'im1-013', 'im1-014']
        selected = assembler.load_selected(assembler.DEFAULT_LIBRARY, assembler.DEFAULT_DXF_SOURCES, codes)
        prepared = assembler.load_and_validate(selected, profile)
        pages = assembler.split_pages(prepared, profile)
        self.assertEqual([[item[0] for item in page] for page in pages], [[code] for code in codes])
        for index, page in enumerate(pages):
            code, _, modelspace, _, _ = page[0]
            placement = assembler.template_placement(code, modelspace)
            self.assertIsNotNone(placement)
            self.assertAlmostEqual(placement['x'], 146.83)
            self.assertAlmostEqual(placement['y'], 72.52)
            anchor = modelspace.doc.entitydb.get(json.loads(
                (ROOT / 'data/cad-template-contracts' / f'{code}.json').read_text(encoding='utf-8')
            )['placement']['anchorHandle'])
            self.assertEqual(anchor.plain_text(), 'ХТ1')
            with tempfile.TemporaryDirectory(prefix='l-placement-test-') as directory:
                output = Path(directory) / 'sheet.dxf'
                di = 'DI24-PNP' if code in {'im1-012', 'im1-014'} else 'DI24-NPN'
                do = 'DOT-PNP' if code in {'im1-013', 'im1-014'} else 'DOR-NO'
                channels = [dict(family=di, address='01', terminals=['01'], deviceRef='PLC', commonDesignation='GND1'),
                            dict(family=do, address='T1' if do == 'DOT-PNP' else 'Q1-1',
                                 terminals=['T1'] if do == 'DOT-PNP' else ['Q1-1', 'Q1-2'],
                                 deviceRef='PLC', commonDesignation='GND3' if do == 'DOT-PNP' else None)]
                instance = dict(id=f'm{index}', tag='M1', channels=channels,
                                cadFieldValues={'processDeviceType': 'Насос', 'marking': 'M1', 'deviceTag': 'M1'},
                                cadFieldSources={'processDeviceType': 'Ques 1:20', 'marking': 'Ques 1:21', 'deviceTag': 'Ques 1:22'},
                                cadHeadRules=[dict(sourceRow=row, code=code, keyDiagram=key, placeholder=placeholder, value=value)
                                              for row, key, placeholder, value in (
                                                  (2, 'Imd-3', '#Process Device Type1', 'Насос'),
                                                  (3, 'Imd-1', '#Marking2', 'M1'),
                                                  (4, 'Imd-2', '#Device tag', 'M1'))] if code == 'im1-011' else [])
                assembler.assemble([page], output, [instance], profile=profile)
                document = ezdxf.readfile(output)
                self.assertFalse(document.audit().has_errors)
                placed = [e for e in document.modelspace().query('MTEXT') if e.plain_text().strip() == 'ХТ1']
                self.assertEqual(len(placed), 1)
                self.assertAlmostEqual(placed[0].dxf.insert.x, 146.83, places=2)
                self.assertAlmostEqual(placed[0].dxf.insert.y, 72.52, places=2)

    def test_four_user_supplied_variants_match_sources_and_io(self):
        library = json.loads((ROOT / 'data/scheme-library.json').read_text(encoding='utf-8'))
        manifest = json.loads((ROOT / 'data/dxf-sources/conversion-manifest.json').read_text(encoding='utf-8'))
        for code, input_family, output_family in (
            ('im1-011', 'DI24-NPN', 'DOR-NO'),
            ('im1-012', 'DI24-PNP', 'DOR-NO'),
            ('im1-013', 'DI24-NPN', 'DOT-PNP'),
            ('im1-014', 'DI24-PNP', 'DOT-PNP'),
        ):
            with self.subTest(code=code):
                source_dwg = ROOT / 'data/cad-sources' / f'{code}.dwg'
                source_dxf = ROOT / 'data/dxf-sources' / f'{code}.dxf'
                registered = next(item for item in library['sources'] if item['code'] == code)
                converted = next(item for item in manifest['converted'] if item['code'] == code)
                contract = json.loads((ROOT / 'data/cad-template-contracts' / f'{code}.json').read_text(encoding='utf-8'))
                self.assertEqual(registered['sha256'], hashlib.sha256(source_dwg.read_bytes()).hexdigest())
                self.assertEqual(converted['sourceSha256'], registered['sha256'])
                self.assertEqual(converted['output']['sha256'], hashlib.sha256(source_dxf.read_bytes()).hexdigest())
                self.assertEqual(contract['sourceSha256'], converted['output']['sha256'])
                self.assertEqual((contract['inputFamily'], contract['outputFamily']), (input_family, output_family))
                self.assertEqual(len(converted['preparation']['removedOrphanHatchHandles']), 23)
                document = ezdxf.readfile(source_dxf)
                self.assertFalse(document.audit().has_errors)
                for field in contract['fields']:
                    entity = document.entitydb.get(field['handle'])
                    self.assertIsNotNone(entity)
                    self.assertEqual((entity.dxftype(), entity.dxf.layer, assembler.mtext_plain(entity)),
                                     ('MTEXT', field['layer'], field['text']))

    def test_four_variants_replace_their_own_io_markers(self):
        for code, di_family, do_family in (
            ('im1-011', 'DI24-NPN', 'DOR-NO'),
            ('im1-012', 'DI24-PNP', 'DOR-NO'),
            ('im1-013', 'DI24-NPN', 'DOT-PNP'),
            ('im1-014', 'DI24-PNP', 'DOT-PNP'),
        ):
            with self.subTest(code=code):
                document = ezdxf.readfile(ROOT / 'data/dxf-sources' / f'{code}.dxf')
                source_sha = hashlib.sha256((ROOT / 'data/dxf-sources' / f'{code}.dxf').read_bytes()).hexdigest()
                channels = [
                    dict(family=di_family, address='07' if di_family == 'DI24-NPN' else '01',
                         terminals=['07' if di_family == 'DI24-NPN' else '01'],
                         deviceRef='PLC', commonDesignation='GND1'),
                    dict(family=do_family, address='Q1-1' if do_family == 'DOR-NO' else 'T1',
                         terminals=['Q1-1', 'Q1-2'] if do_family == 'DOR-NO' else ['T1'],
                         deviceRef='PLC', commonDesignation=None if do_family == 'DOR-NO' else 'GND3'),
                ]
                head = [dict(sourceRow=row, code=code, keyDiagram=key, placeholder=placeholder, value=value)
                        for row, key, placeholder, value in (
                            (2, 'Imd-3', '#Process Device Type1', 'Насос'),
                            (3, 'Imd-1', '#Marking2', 'M1'),
                            (4, 'Imd-2', '#Device tag', 'M1'))] if code == 'im1-011' else []
                trace = assembler.apply_template_contract(code, document.modelspace(), source_sha,
                                                           {'tag': 'M1', 'channels': channels, 'cadHeadRules': head,
                                                            'cadFieldValues': {'processDeviceType': 'Насос', 'marking': 'M1', 'deviceTag': 'M1'},
                                                            'cadFieldSources': {'processDeviceType': 'Ques 1:20', 'marking': 'Ques 1:21', 'deviceTag': 'Ques 1:22'}}, {})
                changed = {item['fieldId']: item['after'] for item in trace}
                self.assertEqual(changed['plc.di'], channels[0]['address'])
                self.assertEqual(changed['plc.do.1'], channels[1]['terminals'][0])
                self.assertEqual(changed['plc.inputCommon'], 'GND1')
                self.assertEqual(changed['header.processDeviceType'], 'Насос')
                self.assertEqual(changed['header.marking'], 'M1')
                self.assertFalse(any(item['status'] == 'unresolved' for item in trace))
                self.assertEqual(changed.get('plc.outputCommon'), 'GND3' if do_family == 'DOT-PNP' else None)
                self.assertEqual(changed.get('plc.do.2'), 'Q1-2' if do_family == 'DOR-NO' else None)
                self.assertFalse(document.audit().has_errors)

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
            trace = []
            assembler.assemble(
                assembler.split_pages(assembler.load_and_validate(selected)),
                output, instances=[{'id': f'm{index}', 'tag': f'M{index}',
                                    'channels': im1_014_channels(), 'cadHeadRules': []}
                                   for index in (1, 2)], parameter_trace=trace,
            )
            document = ezdxf.readfile(output)
            self.assertFalse(document.audit().has_errors)
            marked = {(item['instanceId'], item['fieldId']): item['after'] for item in trace}
            self.assertEqual(marked['m1', 'device.qf.main'], 'QF1')
            self.assertEqual(marked['m2', 'device.qf.main'], 'QF2')
            self.assertEqual(marked['m2', 'device.km.contact'], 'KM2.1')
            self.assertEqual(marked['m1', 'terminal.xt1.1'], '1')
            self.assertEqual(marked['m2', 'terminal.xt1.1'], '3')
            self.assertEqual(marked['m1', 'plc.outputCommon'], 'GND3')
            self.assertTrue(any(item['status'] == 'unresolved' for item in trace if item['fieldId'].startswith('header.')))

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
        self.assertEqual(fields['m1', 'plc.inputCommon'], 'GND1')
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
        instances = api.validate_instances({'instances': [
            dict(id=f'm{index}-{code}', tag=f'M{index}', code=code,
                 **({'channels': im1_014_channels(), 'cadHeadRules': []} if code == 'im1-014' else {}))
            for index in range(8) for code in ['im1-014', 'im2-1']]})
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
                self.assertTrue(all(item['layer'] in {'Слой1', 'XT1', 'XT7', 'Headers'} for item in parameterization['fields']))
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
            instances = [dict(id=f'part-{index}', tag=f'M{index}',
                              **({'channels': im1_014_channels(), 'cadHeadRules': []} if code == 'im1-014' else {}))
                         for index, code in enumerate(codes)]
            assembler.assemble(assembler.split_pages(assembler.load_and_validate(selected)), output, instances=instances)
            document = ezdxf.readfile(output)
            self.assertFalse(document.audit().has_errors)
            self.assertEqual(document.header['$INSUNITS'], 4)
            labels = [item.dxf.text for item in document.modelspace().query('TEXT') if item.dxf.text.startswith('DRAFT')]
            self.assertEqual(len(labels), 4)

if __name__ == '__main__':
    unittest.main()
