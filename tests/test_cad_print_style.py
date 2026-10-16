"""Regression checks for the A3 print reference and normalized IEC library."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf
from ezdxf import bbox


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from fit_cad_print_style import (  # noqa: E402
    build_a3_proof, build_e3_frame, fit_template, normalize_ugo,
    symbol_scale_for_major,
)


DOWNLOADS = Path.home() / 'Downloads'
ARCHIVE = DOWNLOADS / 'L CAD эталон'
TEMPLATE_SOURCE = ARCHIVE / 'im1-011-etalon-before-print-style.dxf'
UGO_SOURCE = ARCHIVE / 'IEC — Чистые УГО — before-print-style.dxf'
FRAME = ROOT / 'data/dxf-frames/sheet-1-1.dxf'
PROFILE = ROOT / 'data/drawing-profiles.json'


class ScaleRuleTests(unittest.TestCase):
    def test_small_reference_and_tall_symbols(self):
        self.assertAlmostEqual(2.6458 * symbol_scale_for_major(2.6458), 5.0)
        self.assertAlmostEqual(13.2292 * symbol_scale_for_major(13.2292), 15.0)
        self.assertAlmostEqual(84.6667 * symbol_scale_for_major(84.6667), 40.0)
        with self.assertRaises(ValueError):
            symbol_scale_for_major(0)


@unittest.skipUnless(TEMPLATE_SOURCE.is_file() and UGO_SOURCE.is_file(),
                     'User CAD sources and their print-style backups are not present')
class PrintReferenceTests(unittest.TestCase):
    def test_etalon_is_centered_and_print_labels_clear_buses(self):
        origin = tuple(json.loads(PROFILE.read_text(encoding='utf-8'))
                       ['electrical']['origin'])
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / 'template.dxf'
            proof = Path(directory) / 'proof.dxf'
            report = fit_template(TEMPLATE_SOURCE, template, origin)
            self.assertEqual(report['parts'], {'power': 63, 'feedback': 16,
                                                'control': 15})
            doc = ezdxf.readfile(template)
            self.assertFalse(doc.audit().has_errors)
            for insert in doc.modelspace().query('INSERT'):
                part = {a.dxf.tag: a.dxf.text for a in insert.attribs}['PART_ID']
                ext = bbox.extents([insert])
                center = (ext.extmin.x + ext.extmax.x) / 2 - origin[0]
                self.assertAlmostEqual(center, report['centersMm'][part], places=3)
            for handle, y in report['headerTopMm'].items():
                text = doc.entitydb[handle]
                self.assertAlmostEqual(text.dxf.insert.y - origin[1], y)
                self.assertEqual(text.dxf.char_height, 4.0)
            frame = Path(directory) / 'frame.dxf'
            build_e3_frame(FRAME, frame, origin)
            styled = ezdxf.readfile(frame)
            original = ezdxf.readfile(FRAME)
            for handle in ('12B0', '12B1', '12B2', '12B3', '135C',
                           '1362', '1364', '1366', '1367'):
                self.assertEqual(styled.entitydb[handle].dxf.start,
                                 original.entitydb[handle].dxf.start)
                self.assertEqual(styled.entitydb[handle].dxf.end,
                                 original.entitydb[handle].dxf.end)
            self.assertEqual(styled.layers.get('L_E3_FRAME').dxf.color, 5)
            self.assertEqual(styled.layers.get('L_NET_PE').dxf.linetype,
                             'DASHED')
            build_a3_proof(template, frame, proof, origin)
            page = ezdxf.readfile(proof)
            self.assertFalse(page.audit().has_errors)
            self.assertEqual(page.layers.get('L_POINTS').dxf.plot, 1)
            self.assertEqual(page.layers.get('L_PART_BOUNDARY').dxf.plot, 0)
            self.assertIn('A3-1to1', [layout.name for layout in page.layouts])

    def test_all_ugo_blocks_are_one_to_one_and_have_separate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'symbols.dxf'
            report = normalize_ugo(UGO_SOURCE, output)
            doc = ezdxf.readfile(output)
            self.assertFalse(doc.audit().has_errors)
            self.assertEqual(report['symbols'], 323)
            self.assertEqual(report['separateLabels'], 312)
            inserts = list(doc.modelspace().query('INSERT'))
            labels = [e for e in doc.modelspace().query('MTEXT')
                      if e.has_xdata('L_UGO_LABEL')]
            self.assertEqual(len(inserts), 323)
            self.assertEqual(len(labels), 312)
            for insert in inserts:
                self.assertEqual(insert.dxf.xscale, 1)
                self.assertEqual(insert.dxf.yscale, 1)
                block = doc.blocks[insert.dxf.name]
                self.assertTrue(block.block.has_xdata('L_UGO_PRINT'))
                size = bbox.extents(block).size
                self.assertGreaterEqual(max(size.x, size.y), 4.999)
                self.assertLessEqual(max(size.x, size.y), 40.001)
            for label in labels:
                self.assertIn(label.dxf.char_height, (3.0, 4.0))
                self.assertEqual(label.dxf.style, 'Standard')
                tags = label.get_xdata('L_UGO_LABEL')
                self.assertEqual(doc.entitydb[tags[1].value].dxf.name, tags[0].value)


if __name__ == '__main__':
    unittest.main()
