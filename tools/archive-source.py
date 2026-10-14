#!/usr/bin/env python3
"""Archive matching patched PPSSPP sources and initialized submodules, never game data."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

KIT_FILES = (
    '.github/workflows/build-ios-shell.yml', '.gitignore', 'COPYING', 'README.md',
    'SHA256SUMS.txt', 'UPSTREAM.txt', 'mc3-directboot.patch', 'tests/test_tools.py',
    'tools/build-macos.sh', 'tools/build-shell-macos.sh',
    'tools/bundle-private-game.py', 'tools/content.py', 'tools/archive-source.py',
)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--kit', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    source = a.source.resolve(strict=True)
    kit = a.kit.resolve(strict=True)
    if a.output.exists():
        raise SystemExit('Refusing to overwrite an existing source archive')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != 'fa50bb1976065c4f8b1b47af227d367fe9771555':
        raise SystemExit('Unexpected upstream source revision')
    revisions = subprocess.check_output(['git', 'submodule', 'status', '--recursive'], cwd=source, text=True)
    if any(line.startswith(('-', '+', 'U')) for line in revisions.splitlines()):
        raise SystemExit('Submodule source revisions are missing or do not match their pins')
    subprocess.run(['git', 'apply', '--reverse', '--check', str(kit / 'mc3-directboot.patch')], cwd=source, check=True)
    # Use tracked files, plus the new patched header and exact generated version file.
    # Recursive submodules are visited explicitly, without collecting their .git files.
    repositories = [source]
    entries = {}
    while repositories:
        repository = repositories.pop()
        records = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=repository).decode().split('\0')
        for record in filter(None, records):
            metadata, name = record.split('\t', 1)
            mode, revision, stage = metadata.split()
            file = repository / name
            if stage != '0':
                raise SystemExit(f'Unresolved source index entry: {name}')
            if mode == '160000':
                if file.is_symlink() or not file.is_dir():
                    raise SystemExit(f'Missing submodule: {name}')
                head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=file, text=True).strip()
                if head != revision:
                    raise SystemExit(f'Submodule revision mismatch: {name}')
                repositories.append(file)
                continue
            if not file.is_file() and not file.is_symlink():
                raise SystemExit(f'Missing source file: {file.relative_to(source)}')
            entries[file.relative_to(source).as_posix()] = file
    for name in ('UI/SingleGame.h', 'build-ios/git-version.cpp'):
        file = source / name
        if not file.is_file() or file.is_symlink():
            raise SystemExit(f'Missing required patched/generated source: {name}')
        entries[name] = file
    with tarfile.open(a.output, 'x:xz') as archive:
        data = ('fa50bb1976065c4f8b1b47af227d367fe9771555\n' + revisions).encode()
        info = tarfile.TarInfo('PPSSPP-SOURCE-REVISIONS.txt')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        for name, file in sorted(entries.items()):
            archive.add(file, arcname='ppsspp/' + name, recursive=False)
        for name in KIT_FILES:
            archive.add(kit / name, arcname='mc3-build-kit/' + name, recursive=False)
    h = hashlib.sha256()
    with a.output.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    Path(str(a.output) + '.sha256').write_text(f'{h.hexdigest()}  {a.output.name}\n')
    print(f'Archived {len(entries)} matching upstream source files and the source kit: {a.output.stat().st_size:,} bytes')

if __name__ == '__main__':
    main()
