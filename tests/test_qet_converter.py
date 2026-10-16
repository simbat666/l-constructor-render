import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

from scripts.qet_to_dxf_library import convert, selected_files


class QetConverterTest(unittest.TestCase):
    def test_power_control_profile_excludes_electronics_and_other_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            wanted = source / "10_electric/10_allpole/200_fuses_protective_gears/breaker.elmt"
            unwanted = source / "10_electric/10_allpole/395_electronics_semiconductors/transistor.elmt"
            connector = source / "10_electric/10_allpole/140_connectors_plugs/60_connectors_electronics/pin.elmt"
            for file in (wanted, unwanted, connector):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            self.assertEqual(selected_files(source, "power-control"), [wanted])

    def test_element_becomes_reopenable_block_with_terminal_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "breaker.elmt").write_text(
                '<definition width="20" height="20"><names><name lang="ru">Автомат</name></names>'
                '<description><line x1="0" y1="-10" x2="0" y2="10"/>'
                '<terminal name="IN" x="0" y="-10" orientation="n"/>'
                '<terminal name="OUT" x="0" y="10" orientation="s"/></description></definition>',
                encoding="utf-8",
            )
            result = convert(source, Path(temporary) / "out")
            index = json.loads(Path(result["index"]).read_text(encoding="utf-8"))
            row = index["symbols"][0]
            doc = ezdxf.readfile(result["dxf"])
            line = next(iter(doc.blocks[row["block"]]))
            self.assertEqual(result["count"], 1)
            self.assertEqual(row["terminals"][0]["y"], 10)
            self.assertEqual(line.dxf.start.y, 10)
            self.assertEqual(line.dxf.end.y, -10)
            self.assertFalse(doc.audit().has_errors)


if __name__ == "__main__":
    unittest.main()
