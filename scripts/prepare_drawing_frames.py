"""Offline-only conversion of the two supplied frame DWGs; never called by HTTP."""
import argparse
import json
from pathlib import Path
import tempfile
import zipfile
from convert_dwg_library import convert_one, sha256

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    output = ROOT / 'data' / 'dxf-frames'
    records = []
    with zipfile.ZipFile(args.archive) as archive, tempfile.TemporaryDirectory(prefix='l-frames-') as directory:
        sources = Path(directory)
        for name in ['Sheet 1.1', 'Sheet 1.2']:
            source = sources / f'{name}.dwg'
            source.write_bytes(archive.read(source.name))
            record = {'code': name.lower().replace(' ', '-').replace('.', '-'), 'localFile': source.name, 'sha256': sha256(source)}
            records.append(convert_one(record, sources, output, False))
            print(f'Converted {name}', flush=True)
    (output / 'conversion-manifest.json').write_text(json.dumps({'archive': args.archive.name, 'converted': records}, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
