"""Rebuild display patches from locked raw bytes, preserving missing EOF newlines."""
import difflib
import hashlib
import json
from pathlib import Path
import sys


def unified(source, image, libc):
    lines = difflib.unified_diff(source.decode('utf-8').splitlines(keepends=True),
                                 image.decode('utf-8').splitlines(keepends=True),
                                 fromfile='fixed-source', tofile='image-' + libc)
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                   for line in lines)


def main():
    source_path, output_path = map(Path, sys.argv[1:])
    rows = json.loads(source_path.read_text(encoding='utf-8'))
    for row in rows.values():
        source = bytes.fromhex(row['source_hex'])
        if hashlib.sha256(source).hexdigest() != row['source_sha256']:
            raise ValueError('source hash mismatch')
        for libc, entry in row['image'].items():
            image = bytes.fromhex(entry['script_hex'])
            if hashlib.sha256(image).hexdigest() != entry['sha256']:
                raise ValueError('image hash mismatch')
            entry['diff'] = unified(source, image, libc)
    with output_path.open('x', encoding='utf-8', newline='\n') as output:
        output.write(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
