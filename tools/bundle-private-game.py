#!/usr/bin/env python3
"""Add one local MC3 ISO to an unsigned ISO-boot IPA. No signing or networking."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import stat
import struct
import unicodedata
import zipfile
from content import CHUNK_SIZE, make_manifest, open_regular, sha256

PREFIX = 'Payload/MC3.app/'
MAX_SHELL_BYTES = 1024 * 1024 * 1024
MAX_SHELL_ENTRIES = 65536

def normalized(name):
    return unicodedata.normalize('NFC', name.rstrip('/')).casefold()

def safe_name(name):
    if not name or '\\' in name or ':' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError(f'Unsafe archive path: {name!r}')
    core = name[:-1] if name.endswith('/') else name
    parts = core.split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts):
        raise ValueError(f'Unsafe archive path: {name!r}')
    if PurePosixPath(core).is_absolute():
        raise ValueError(f'Unsafe archive path: {name!r}')
    return core


def is_symlink(entry):
    return entry.create_system == 3 and stat.S_ISLNK(entry.external_attr >> 16)


def check_main_signature(original, entry):
    """Reject an embedded Mach-O code signature even without _CodeSignature files."""
    with original.open(entry) as source:
        first = source.read(32)
        if len(first) < 4:
            raise ValueError('Empty or truncated app executable')
        magic = first[:4]
        endian, bits = {b'\xcf\xfa\xed\xfe': ('<', 64), b'\xce\xfa\xed\xfe': ('<', 32),
                        b'\xfe\xed\xfa\xcf': ('>', 64), b'\xfe\xed\xfa\xce': ('>', 32)}.get(magic, (None, None))
        if endian is None:
            # The expected build is thin arm64. Fat files complicate signature inspection.
            if magic in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
                raise ValueError('Use the expected thin arm64 unsigned shell, not a universal executable')
            return
        header_size = 32 if bits == 64 else 28
        if len(first) < header_size:
            raise ValueError('Truncated Mach-O header')
        count, size = struct.unpack_from(endian + 'II', first, 16)
        if count > 4096 or size > 16 * 1024 * 1024 or size > entry.file_size - header_size:
            raise ValueError('Invalid Mach-O load commands')
        source.seek(header_size)
        commands = source.read(size)
        if len(commands) != size:
            raise ValueError('Truncated Mach-O load commands')
        offset = 0
        for _ in range(count):
            if offset + 8 > size:
                raise ValueError('Truncated Mach-O load command')
            command, length = struct.unpack_from(endian + 'II', commands, offset)
            if length < 8 or offset + length > size:
                raise ValueError('Invalid Mach-O load command length')
            if command == 0x1d:
                if length < 16:
                    raise ValueError('Invalid Mach-O signature command')
                _, signature_size = struct.unpack_from(endian + 'II', commands, offset + 8)
                if signature_size:
                    raise ValueError('Use the clean unsigned shell; executable contains a code signature')
            offset += length
        if offset != size:
            raise ValueError('Inconsistent Mach-O command table')


def validate_shell(original):
    entries = original.infolist()
    if not entries or len(entries) > MAX_SHELL_ENTRIES or sum(e.file_size for e in entries) > MAX_SHELL_BYTES:
        raise ValueError('Shell archive exceeds safe entry/size bounds')
    names = {}
    for entry in entries:
        name = entry.filename
        if entry.orig_filename != name:
            raise ValueError('Unsafe archive path contains a NUL byte')
        safe_name(name)
        canonical = normalized(name)
        if canonical in names:
            raise ValueError('Duplicate or case-colliding archive entries')
        names[canonical] = entry
        if name not in ('Payload/', PREFIX) and not name.startswith(PREFIX):
            raise ValueError('Shell contains unexpected files or sibling apps')
        relative_parts = [p.casefold() for p in name[len(PREFIX):].rstrip('/').split('/')]
        if any(p in ('mc3content', '_codesignature', 'embedded.mobileprovision', 'coderesources') for p in relative_parts):
            raise ValueError('Use the clean, unsigned, content-free shell')
        if entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError('Encrypted or unsupported shell ZIP compression')
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise ValueError('Special filesystem nodes are not allowed in the shell')
        if entry.is_dir() and (entry.file_size or kind not in (0, stat.S_IFDIR)):
            raise ValueError('Invalid archive directory metadata')
        if kind == stat.S_IFDIR and not entry.is_dir():
            raise ValueError('Archive directory is missing its trailing slash')
        if name in ('Payload/', PREFIX) and is_symlink(entry):
            raise ValueError('App roots cannot be symlinks')
        if is_symlink(entry):
            if not 0 < entry.file_size <= 4096:
                raise ValueError('Invalid shell symlink size')
            try:
                target = original.read(entry).decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('Invalid shell symlink target') from exc
            if target.startswith('/') or '\\' in target or ':' in target or any(ord(c) < 32 for c in target):
                raise ValueError('Unsafe shell symlink target')
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if not resolved.startswith(PREFIX) or '/mc3content' in resolved.casefold():
                raise ValueError('Shell symlink escapes the app or points at private content')
    for entry in entries:
        parts = entry.filename.rstrip('/').split('/')
        for count in range(1, len(parts)):
            ancestor = names.get(normalized('/'.join(parts[:count])))
            if ancestor is not None and not ancestor.is_dir():
                raise ValueError('Archive entry has a file or symlink as a parent directory')
    for required in ('Info.plist', 'PPSSPP'):
        entry = names.get(normalized(PREFIX + required))
        if entry is None or entry.filename != PREFIX + required or entry.is_dir() or is_symlink(entry):
            raise ValueError('Not the expected MC3 unsigned shell IPA')
    plist = original.getinfo(PREFIX + 'Info.plist')
    if plist.file_size > 1024 * 1024:
        raise ValueError('Shell Info.plist is too large')
    try:
        info = plistlib.loads(original.read(plist))
    except Exception as exc:
        raise ValueError('Invalid shell Info.plist') from exc
    if not isinstance(info, dict) or info.get('CFBundleIdentifier') != 'local.mc3.directboot' or info.get('CFBundleExecutable') != 'PPSSPP':
        raise ValueError('Unexpected app identity/executable')
    if type(info.get('MC3ContentSchema')) is not int or info['MC3ContentSchema'] != 2 or info.get('MC3BootMode') != 'iso' or info.get('CFBundleVersion') != '12004.2':
        raise ValueError('Use the schema-2 ISO-boot shell (MC3BootMode=iso, build12004.2)')
    check_main_signature(original, original.getinfo(PREFIX + 'PPSSPP'))
    return entries


def copied_entry(entry):
    result = copy.copy(entry)
    # ZipInfo retains ZIP64 extras from the source; discard stale size/offset fields.
    extra = bytearray()
    position = 0
    while position < len(entry.extra):
        if position + 4 > len(entry.extra):
            raise ValueError('Malformed ZIP extra field')
        kind, size = struct.unpack_from('<HH', entry.extra, position)
        end = position + 4 + size
        if end > len(entry.extra):
            raise ValueError('Truncated ZIP extra field')
        if kind != 1:
            extra.extend(entry.extra[position:end])
        position = end
    result.extra = bytes(extra)
    return result


def private_entry(name, size=0):
    entry = zipfile.ZipInfo(PREFIX + 'MC3Content/' + name)
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.create_system = 3
    entry.external_attr = 0o100444 << 16
    entry.file_size = size
    return entry


def package(shell, game_iso, output):
    shell, game_iso, output = Path(shell), Path(game_iso), Path(output)
    sidecar = Path(str(output) + '.sha256')
    if os.path.lexists(output) or os.path.lexists(sidecar):
        raise ValueError('Output or SHA-256 sidecar already exists; use a new filename')
    # Validate identity before hashing a multi-gigabyte private input.
    created = False
    sidecar_created = False
    try:
        with open_regular(shell) as shell_stream, zipfile.ZipFile(shell_stream) as original:
            entries = validate_shell(original)
            manifest = make_manifest(game_iso)
            with output.open('xb') as output_stream:
                created = True
                with zipfile.ZipFile(output_stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
                    target.comment = original.comment
                    for entry in entries:
                        # Preserve executable modes, symlinks, timestamps, comments and safe extras.
                        with original.open(entry) as source, target.open(copied_entry(entry), 'w', force_zip64=True) as dest:
                            for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                                dest.write(chunk)
                    target.writestr(private_entry('manifest.json'), json.dumps(manifest, indent=2) + '\n')
                    digest = hashlib.sha256()
                    size = 0
                    entry = private_entry('game.iso', manifest['iso']['size'])
                    with open_regular(game_iso) as source, target.open(entry, 'w', force_zip64=True) as dest:
                        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                            size += len(chunk)
                            if size > manifest['iso']['size']:
                                raise ValueError('ISO changed during packaging')
                            digest.update(chunk)
                            dest.write(chunk)
                    if digest.hexdigest() != manifest['iso']['sha256'] or size != manifest['iso']['size']:
                        raise ValueError('ISO changed during packaging')
        # Decompress every entry for CRC checking. Rehash the ISO from the produced IPA too.
        with zipfile.ZipFile(output) as result:
            bad = result.testzip()
            if bad:
                raise ValueError(f'Corrupt packaged member: {bad}')
            digest = hashlib.sha256()
            with result.open(PREFIX + 'MC3Content/game.iso') as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                    digest.update(chunk)
            if digest.hexdigest() != manifest['iso']['sha256']:
                raise ValueError('Packaged ISO failed SHA-256 readback')
        with sidecar.open('x', encoding='utf-8') as out:
            sidecar_created = True
            out.write(f'{sha256(output)}  {output.name}\n')
        return manifest
    except BaseException:
        # Remove only artifacts this invocation created; never overwrite prior files.
        if created:
            output.unlink(missing_ok=True)
        if sidecar_created:
            sidecar.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shell-ipa', type=Path, required=True)
    parser.add_argument('--game-iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = package(args.shell_ipa, args.game_iso, args.output)
    print(f'Created private unsigned IPA: {args.output} ({manifest["iso"]["size"]:,} ISO bytes).')
    print('It still requires legitimate signing and physical-iPhone testing. Nothing was uploaded.')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SystemExit(f'Packaging failed: {exc}')
