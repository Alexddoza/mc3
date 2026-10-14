#!/usr/bin/env python3
"""Add local game data to the unsigned source-only IPA, without Mac/signing/network access."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import zipfile
from content import make_manifest, sha256

PREFIX = 'Payload/MC3.app/'

def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError(f'Unsafe archive path: {name}')

def package(shell, root, output):
    shell, root, output = Path(shell), Path(root), Path(output)
    if output.exists():
        raise ValueError('Output already exists; use a new filename')
    manifest = make_manifest(root)
    with zipfile.ZipFile(shell) as original:
        names = original.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        if PREFIX + 'PPSSPP' not in names or PREFIX + 'Info.plist' not in names:
            raise ValueError('Not the expected MC3 unsigned shell IPA')
        info = plistlib.loads(original.read(PREFIX + 'Info.plist'))
        if info.get('CFBundleIdentifier') != 'local.mc3.directboot' or info.get('CFBundleExecutable') != 'PPSSPP':
            raise ValueError('Unexpected app identity/executable')
        for name in names:
            safe_name(name)
            if name not in ('Payload/', PREFIX) and not name.startswith(PREFIX):
                raise ValueError('Shell contains unexpected files or sibling apps')
            if name.startswith(PREFIX + 'MC3Content/') or '/_CodeSignature/' in name or name.endswith('/embedded.mobileprovision'):
                raise ValueError('Use the clean, unsigned, content-free shell')
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
            # Reuse each ZipInfo so executable mode bits and framework symlinks survive.
            for entry in original.infolist():
                with original.open(entry) as source, target.open(entry, 'w', force_zip64=True) as dest:
                    shutil.copyfileobj(source, dest, 4 * 1024 * 1024)
            target.writestr(PREFIX + 'MC3Content/manifest.json', json.dumps(manifest, indent=2) + '\n')
            for item in manifest['files']:
                name = PREFIX + 'MC3Content/game-root/' + item['path']
                entry = zipfile.ZipInfo(name)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3
                entry.external_attr = (0o100444 << 16)
                digest = hashlib.sha256()
                count = 0
                with (root / item['path']).open('rb') as source, target.open(entry, 'w', force_zip64=True) as dest:
                    for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        digest.update(chunk); count += len(chunk); dest.write(chunk)
                if digest.hexdigest() != item['sha256'] or count != item['size']:
                    raise ValueError(f'Content changed during packaging: {item["path"]}')
    # Read back all archive members, checking ZIP CRC; full source hashes were checked above.
    with zipfile.ZipFile(output) as result:
        bad = result.testzip()
        if bad:
            raise ValueError(f'Corrupt packaged member: {bad}')
    Path(str(output) + '.sha256').write_text(f'{sha256(output)}  {output.name}\n')
    return manifest

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--shell-ipa', type=Path, required=True)
    p.add_argument('--game-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    manifest = package(a.shell_ipa, a.game_root, a.output)
    print(f'Created private unsigned IPA: {a.output} ({manifest["file_count"]} game files).')
    print('It still requires legitimate signing and physical-iPhone testing. Nothing was uploaded.')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f'Packaging failed: {exc}. If a partial output exists, do not use it.')
