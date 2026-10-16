"""Regression checks for the user supplied CAD reference preparation."""

import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_cad_reference import prepare_template, prepare_ugo  # noqa: E402


DOWNLOADS = Path.home() / 'Downloads'
SOURCE_DWG = DOWNLOADS / 'im1-011-layer-template.dwg'
SOURCE_UGO = DOWNLOADS / 'L CAD эталон' / 'IEC — Чистые УГО — исходный.dxf'
REFERENCE_DXF = DOWNLOADS / 'L CAD эталон' / 'im1-011-source-converted.dxf'


@unittest.skipUnless(all(p.is_file() for p in (SOURCE_DWG, SOURCE_UGO, REFERENCE_DXF)),
                     'User supplied CAD reference files are not present')
class CadReferenceTests(unittest.TestCase):
    def test_template_fields_parts_and_print_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'template.dxf'
            contract = prepare_template(REFERENCE_DXF, output, SOURCE_DWG)
            doc = ezdxf.readfile(output)
            self.assertFalse(doc.audit().has_errors)
            self.assertEqual(set(contract['parts']), {'power', 'feedback', 'control'})
            self.assertEqual([contract['parts'][name]['order'] for name in
                              ('power', 'feedback', 'control')], ['10', '20', '30'])
            self.assertEqual(len(contract['fields']), 13)
            self.assertEqual(next(field['expectedText'] for field in contract['fields']
                                  if field['role'] == 'plc.do.1'), '#DO1')
            self.assertEqual(contract['untypedPointCount'], 7)
            self.assertEqual(doc.layers.get('L_POINTS').dxf.plot, 1)
            self.assertEqual(doc.layers.get('L_PART_BOUNDARY').dxf.plot, 0)
            self.assertEqual(doc.layers.get('L_DRAWING').dxf.lineweight, 25)
            self.assertEqual(doc.layers.get('L_UGO').dxf.lineweight, 25)
            self.assertTrue(all(e.dxf.get('lineweight', -1) == -1 for e in doc.modelspace()))

    def test_ugo_labels_are_separate_mtext_paired_with_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'symbols.dxf'
            report = prepare_ugo(SOURCE_UGO, output)
            doc = ezdxf.readfile(output)
            self.assertFalse(doc.audit().has_errors)
            self.assertEqual(doc.header['$INSUNITS'], 4)
            self.assertEqual(report['symbolCount'], 323)
            self.assertEqual(report['labelCount'], 312)
            self.assertEqual({e.dxf.layer for e in doc.modelspace()}, {'L_UGO', 'L_LABELS'})
            for insert in doc.modelspace().query('INSERT'):
                self.assertEqual(insert.dxf.layer, 'L_UGO')
                self.assertFalse(insert.attribs)
                self.assertTrue(all(e.dxf.layer == 'L_UGO' for e in doc.blocks[insert.dxf.name]))
            labels = [e for e in doc.modelspace().query('MTEXT') if e.has_xdata('L_UGO_LABEL')]
            self.assertEqual(len(labels), 312)
            self.assertTrue(all(e.dxf.layer == 'L_LABELS' and e.plain_text().startswith('#')
                                and e.dxf.char_height == 1.8 for e in labels))
            paired = {e.get_xdata('L_UGO_LABEL')[1].value for e in labels}
            self.assertEqual(len(paired), 312)
            self.assertTrue(all(doc.entitydb.get(handle) is not None for handle in paired))
            qf = next(e for e in labels if e.plain_text() == '#QF1')
            self.assertEqual([tag.value for tag in qf.get_xdata('L_UGO_LABEL')[:2]],
                             ['IEC_301', next(e.dxf.handle for e in doc.modelspace().query('INSERT')
                                               if e.dxf.name == 'IEC_301')])


if __name__ == '__main__':
    unittest.main()
