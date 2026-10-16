import importlib.util
import unittest
from pathlib import Path
import ezdxf

path=Path(__file__).resolve().parents[1]/'scripts/prepare_iec_scheme_template.py'
spec=importlib.util.spec_from_file_location('iec_kit',path)
kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)

class IECPreparationTests(unittest.TestCase):
    def test_a3_layout_has_valid_paperspace_viewport_and_locked_scale(self):
        d=kit.setup();kit.frame(d)
        viewports=list(d.layouts.get('Layout1').query('VIEWPORT'))
        self.assertEqual(next(v for v in viewports if v.dxf.id==1).dxf.layer,'0')
        vp=next(v for v in viewports if v.dxf.id!=1)
        self.assertEqual(vp.dxf.height,vp.dxf.view_height)
        self.assertTrue(vp.dxf.flags & 16384)

    def test_closed_body_is_not_a_port(self):
        d=ezdxf.new();m=d.modelspace()
        for a,b in [((0,0),(5,0)),((5,0),(5,5)),((5,5),(0,5)),((0,5),(0,0))]:m.add_line(a,b)
        self.assertEqual(kit.docking(list(m)),[])

    def test_coil_docking_is_grid_aligned_and_labels_editable(self):
        source=ezdxf.new();b=source.blocks.new('IEC_083')
        for a,c in [((0,10),(0,2)),((0,-2),(0,-10)),((-2,2),(2,2)),((2,2),(2,-2)),((2,-2),(-2,-2)),((-2,-2),(-2,2))]:b.add_line(a,c)
        d=kit.setup();r={'id':'083','tag':'K1','ru':'Катушка','code':'K'}
        contract=kit.normalized_block(d,source,r)
        self.assertEqual(len(contract['ports']),2)
        for p in contract['ports']:
            for x in p['position'][:2]:self.assertAlmostEqual(x/kit.GRID,round(x/kit.GRID))
        ins=kit.insert(d,contract,(50,50),'KM1')
        self.assertEqual(ins.get_attrib_text('TAG'),'KM1')
        self.assertEqual(ins.get_attrib_text('NAME_RU'),'Катушка')
        self.assertTrue(ins.get_attrib('NAME_RU').is_invisible)
        self.assertEqual({a.dxf.layer for a in ins.attribs},{'UGO_LABELS'})
        self.assertEqual(d.header['$INSUNITS'],4)
        self.assertFalse(d.audit().has_errors)

if __name__=='__main__':unittest.main()
