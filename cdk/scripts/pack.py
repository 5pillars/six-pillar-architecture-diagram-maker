#!/usr/bin/env python3
"""Pack the inventory script into a base64 tar.xz for embedding in the CodeBuild buildspec.

The output is reproducible: same files in, same text out, so the committed template only
changes when the script changes.

Usage: python3 pack.py <project root>
"""
import base64
import io
import lzma
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
files = [root / 'collect_inventory.py', *sorted((root / 'inventory').glob('*.py'))]

buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
    for path in files:
        data = path.read_bytes()
        info = tarfile.TarInfo(str(path.relative_to(root)))
        info.size, info.mode, info.mtime = len(data), 0o644, 0
        archive.addfile(info, io.BytesIO(data))

packed = lzma.compress(buffer.getvalue(), format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME)
sys.stdout.write(base64.b64encode(packed).decode('ascii'))
