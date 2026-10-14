#!/usr/bin/env python3
"""Verify/package only a local, user-owned extracted MC3 disc. No networking."""
import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

DISC_ID = 'ULUS10021'
REQUIRED = ('UMD_DATA.BIN', 'PSP_GAME/PARAM.SFO', 'PSP_GAME/SYSDIR/EBOOT.BIN', 'PSP_GAME/USRDIR/pspasset.dat')

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def sfo_disc_id(path):
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b'\x00PSF':
        raise ValueError('PARAM.SFO is not a valid SFO file')
    _, _, keys, values, count = struct.unpack_from('<5I', data)
    if count > 1024 or 20 + count * 16 > len(data):
        raise ValueError('Invalid SFO table')
    for n in range(count):
        key_offset, fmt, size, capacity, value_offset = struct.unpack_from('<HHIII', data, 20 + n * 16)
        start = keys + key_offset
        if start >= len(data):
            raise ValueError('Invalid SFO key offset')
        end = data.find(b'\0', start)
        if end < 0:
            raise ValueError('Unterminated SFO key')
        if data[start:end] == b'DISC_ID':
            start = values + value_offset
            if fmt != 0x0204 or size > capacity or start + size > len(data):
                raise ValueError('Invalid SFO DISC_ID entry')
            return data[start:start + size].rstrip(b'\0').decode('ascii')
    raise ValueError('SFO does not identify a disc')

def make_manifest(root):
    root = Path(root).resolve(strict=True)
    for relative in REQUIRED:
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError(f'Missing or empty required file: {relative}')
    disc_id = sfo_disc_id(root / 'PSP_GAME/PARAM.SFO')
    if disc_id != DISC_ID:
        raise ValueError(f'Wrong game edition: {disc_id}; expected {DISC_ID}')
    with (root / 'PSP_GAME/SYSDIR/EBOOT.BIN').open('rb') as stream:
        if stream.read(4) not in (b'~PSP', b'\x7fELF'):
            raise ValueError('EBOOT.BIN is neither a PSP executable nor an ELF')
    files = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f'Symlinks are not permitted in private game content: {relative}')
        if not path.is_file():
            continue
        if relative != 'UMD_DATA.BIN' and not relative.startswith('PSP_GAME/'):
            raise ValueError(f'Unexpected top-level content: {relative}')
        if path.suffix.lower() in ('.zip', '.7z', '.rar', '.iso', '.cso', '.ipa'):
            raise ValueError(f'Nested archive is not game-root content: {relative}')
        if relative.startswith('PSP_GAME/UPDATE/'):
            raise ValueError('Remove PSP firmware updater files; they are not needed for this app')
        files.append({'path': relative, 'size': path.stat().st_size, 'sha256': sha256(path)})
    return {'schema': 1, 'disc_id': disc_id, 'file_count': len(files), 'total_bytes': sum(f['size'] for f in files), 'files': files}

def verify(root, manifest):
    actual = make_manifest(root)
    if actual != manifest:
        expected = {f['path']: f for f in manifest['files']}
        got = {f['path']: f for f in actual['files']}
        differences = [name for name in sorted(expected.keys() | got.keys()) if expected.get(name) != got.get(name)]
        raise ValueError('Content does not match SHA-256 manifest: ' + ', '.join(differences[:8]))
    return actual

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--copy-to', type=Path, help='New private MC3Content directory; refuses overwrite')
    args = parser.parse_args()
    manifest = make_manifest(args.game_root)
    if args.verify:
        manifest = verify(args.game_root, json.loads(args.manifest.read_text()))
    else:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    if args.copy_to:
        if args.copy_to.exists():
            raise ValueError(f'Destination already exists: {args.copy_to}')
        args.copy_to.mkdir(parents=True)
        shutil.copytree(args.game_root, args.copy_to / 'game-root')
        verify(args.copy_to / 'game-root', manifest)
        (args.copy_to / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Verified {manifest["file_count"]} files, {manifest["total_bytes"]:,} bytes, {manifest["disc_id"]}.')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f'Content check failed: {exc}')
