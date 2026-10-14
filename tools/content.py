#!/usr/bin/env python3
"""Validate a local, user-owned MC3 ISO and create a schema-2 manifest. No networking."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import struct

DISC_ID = 'ULUS10021'
BLOCK = 2048
MIN_ISO_SIZE = 64 * 1024
MAX_ISO_SIZE = 2 * 1024 * 1024 * 1024
MAX_DIRECTORY_SIZE = 8 * 1024 * 1024
MAX_CHECK_BYTES = 32 * 1024 * 1024
MAX_DIRECTORIES = 128
MAX_FILES = 16384
SMALL_CHECK = 64 * 1024
CHUNK_SIZE = 4 * 1024 * 1024
REQUIRED = ('UMD_DATA.BIN', 'PSP_GAME/PARAM.SFO', 'PSP_GAME/SYSDIR/EBOOT.BIN', 'PSP_GAME/USRDIR/PSPASSET.DAT')
ARCHIVE_SUFFIXES = ('.ZIP', '.7Z', '.RAR', '.ISO', '.CSO', '.CHD', '.IPA', '.TAR', '.GZ', '.BZ2', '.XZ')

@contextmanager
def open_regular(path):
    """Reject symlink/device inputs; do not follow a leaf-path replacement race."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f'Symlinks are not permitted for input files: {path}')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'Input is not a regular file: {path}')
        yield stream

def snapshot(stream):
    s = os.fstat(stream.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

def stream_hash(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(CHUNK_SIZE), b''):
        digest.update(chunk)
    return digest.hexdigest()

def sha256(path):
    with open_regular(path) as stream:
        return stream_hash(stream)

def both16(data, offset):
    le, be = struct.unpack_from('<H', data, offset)[0], struct.unpack_from('>H', data, offset + 2)[0]
    if le != be:
        raise ValueError('ISO little/big-endian 16-bit values disagree')
    return le

def both32(data, offset):
    le, be = struct.unpack_from('<I', data, offset)[0], struct.unpack_from('>I', data, offset + 4)[0]
    if le != be:
        raise ValueError('ISO little/big-endian 32-bit values disagree')
    return le

@dataclass(frozen=True)
class Extent:
    offset: int
    size: int
    directory: bool

class ISO9660:
    """Strict bounded primary-volume reader, independent of third-party ISO packages.

    ISO level 4 ASCII names and a supplementary enhanced descriptor are supported.
    Joliet/Rock Ridge names are not used: PPSSPP reads the primary ISO namespace.
    Multi-extent, interleaved and extended-attribute files are deliberately rejected.
    """
    def __init__(self, stream):
        self.stream = stream
        self.size = os.fstat(stream.fileno()).st_size
        if not MIN_ISO_SIZE <= self.size <= MAX_ISO_SIZE or self.size % BLOCK:
            raise ValueError('ISO size must be 64 KiB–2 GiB and aligned to 2048-byte sectors')
        self.files = {}
        self.directories = []
        self.allocations = []
        pvd = None
        end = None
        for sector in range(16, 48):
            descriptor = self.read(sector * BLOCK, BLOCK)
            kind = descriptor[0]
            if descriptor[1:6] != b'CD001' or descriptor[6] not in (1, 2):
                raise ValueError('Invalid ISO9660 volume descriptor')
            if kind not in (0, 1, 2, 255):
                raise ValueError('Unsupported ISO9660 volume descriptor')
            if kind == 1:
                if pvd is not None or descriptor[6] != 1:
                    raise ValueError('Duplicate or invalid primary volume descriptor')
                pvd = descriptor
            if kind == 255:
                end = (sector + 1) * BLOCK
                break
        if pvd is None or end is None:
            raise ValueError('Missing ISO primary descriptor or descriptor terminator')
        if both32(pvd, 80) * BLOCK != self.size:
            raise ValueError('ISO declared volume size does not match the input file')
        if both16(pvd, 120) != 1 or both16(pvd, 124) != 1 or both16(pvd, 128) != BLOCK:
            raise ValueError('ISO must be a single-volume 2048-byte-sector image')
        path_size = both32(pvd, 132)
        if not 1 <= path_size <= MAX_DIRECTORY_SIZE:
            raise ValueError('Invalid ISO path-table size')
        for position, endian, optional in ((140, '<I', False), (144, '<I', True), (148, '>I', False), (152, '>I', True)):
            location = struct.unpack_from(endian, pvd, position)[0]
            if not (optional and location == 0):
                self.bound(location * BLOCK, path_size)
        self.descriptors = Extent(16 * BLOCK, end - 16 * BLOCK, False)
        self.allocations.append((0, end, 'ISO system area and descriptors'))
        root, identifier = self.record(pvd[156:190])
        if identifier != b'\0' or not root.directory:
            raise ValueError('Invalid ISO root directory record')
        self.walk(root, '', root, 0)
        for required in REQUIRED:
            if required not in self.files or self.files[required].size == 0:
                raise ValueError(f'Missing or empty required ISO file: {required}')
        # No file/dir can alias another allocation, including descriptor sectors.
        ordered = sorted(self.allocations)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                raise ValueError(f'Overlapping ISO extents: {previous[2]} and {current[2]}')

    def bound(self, offset, size):
        if offset < 0 or size < 0 or offset > self.size or size > self.size - offset:
            raise ValueError('ISO extent is outside the image')

    def read(self, offset, size):
        self.bound(offset, size)
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError('Truncated ISO extent')
        return data

    def record(self, record):
        if len(record) < 34 or record[0] != len(record) or record[32] == 0:
            raise ValueError('Invalid ISO directory record length')
        identifier_size = record[32]
        minimum = 33 + identifier_size + (identifier_size % 2 == 0)
        if minimum > len(record):
            raise ValueError('Truncated ISO directory identifier')
        if record[1] or record[26] or record[27] or record[25] & ~3:
            raise ValueError('Unsupported extended, multi-extent or interleaved ISO file')
        if both16(record, 28) != 1:
            raise ValueError('Invalid ISO extent volume sequence')
        offset, size = both32(record, 2) * BLOCK, both32(record, 10)
        self.bound(offset, size)
        return Extent(offset, size, bool(record[25] & 2)), record[33:33 + identifier_size]

    def allocate(self, extent, path):
        if extent.size:
            allocated = ((extent.size + BLOCK - 1) // BLOCK) * BLOCK
            self.bound(extent.offset, allocated)
            self.allocations.append((extent.offset, extent.offset + allocated, path))

    def walk(self, directory, path, parent, depth):
        if depth > 16 or len(self.directories) >= MAX_DIRECTORIES:
            raise ValueError('ISO directory count or nesting exceeds safe bounds')
        if not BLOCK <= directory.size <= MAX_DIRECTORY_SIZE or directory.size % BLOCK:
            raise ValueError('Invalid or oversized ISO directory')
        self.allocate(directory, path or '/')
        self.directories.append(directory)
        if sum(d.size for d in self.directories) > MAX_CHECK_BYTES - 5 * SMALL_CHECK:
            raise ValueError('ISO directory checks exceed safe total size')
        data = self.read(directory.offset, directory.size)
        position = 0
        names = set()
        specials = set()
        children = []
        while position < len(data):
            length = data[position]
            if not length:
                next_sector = (position // BLOCK + 1) * BLOCK
                if any(data[position:next_sector]):
                    raise ValueError('Nonzero bytes after ISO directory sector padding')
                position = next_sector
                continue
            if position % BLOCK + length > BLOCK or position + length > len(data):
                raise ValueError('ISO directory record crosses a sector boundary')
            extent, identifier = self.record(data[position:position + length])
            position += length
            if identifier in (b'\0', b'\1'):
                expected = directory if identifier == b'\0' else parent
                if identifier in specials or extent != expected:
                    raise ValueError('Invalid duplicate, self or parent ISO directory record')
                specials.add(identifier)
                continue
            try:
                name = identifier.decode('ascii')
            except UnicodeDecodeError as exc:
                raise ValueError('ISO identifiers must use the ASCII primary namespace') from exc
            if name.endswith(';1'):
                name = name[:-2]
            if not name or name in ('.', '..') or any(ord(c) < 32 or ord(c) > 126 or c in '/\\:;' for c in name):
                raise ValueError('Unsafe ISO file identifier')
            normalized = name.upper()
            if normalized in names:
                raise ValueError('Duplicate or case-colliding ISO directory entry')
            names.add(normalized)
            relative = f'{path}/{normalized}' if path else normalized
            if len(relative) > 255:
                raise ValueError('ISO path is too long')
            if not path and relative not in ('PSP_GAME', 'UMD_DATA.BIN'):
                raise ValueError(f'Unexpected top-level ISO content: {relative}')
            if not path and extent.directory != (relative == 'PSP_GAME'):
                raise ValueError('Unexpected top-level ISO file type')
            if relative == 'PSP_GAME/UPDATE' or relative.startswith('PSP_GAME/UPDATE/'):
                raise ValueError('Remove PSP firmware updater files before making the private ISO')
            if relative.endswith(ARCHIVE_SUFFIXES):
                raise ValueError(f'Nested archive is not game content: {relative}')
            if extent.directory:
                children.append((extent, relative))
            else:
                if len(self.files) >= MAX_FILES:
                    raise ValueError('ISO file count exceeds safe bounds')
                self.allocate(extent, relative)
                self.files[relative] = extent
        if specials != {b'\0', b'\1'}:
            raise ValueError('Missing ISO self or parent directory record')
        for child, relative in children:
            self.walk(child, relative, directory, depth + 1)


def sfo_disc_id(data):
    if len(data) < 20 or len(data) > SMALL_CHECK or data[:4] != b'\x00PSF':
        raise ValueError('PARAM.SFO is not a valid bounded SFO file')
    _, version, keys, values, count = struct.unpack_from('<5I', data)
    table_end = 20 + count * 16
    if version != 0x101 or not 1 <= count <= 1024 or not table_end <= keys < values <= len(data):
        raise ValueError('Invalid SFO table bounds')
    result = None
    seen = set()
    for n in range(count):
        key_offset, fmt, size, capacity, value_offset = struct.unpack_from('<HHIII', data, 20 + n * 16)
        start = keys + key_offset
        if not keys <= start < values:
            raise ValueError('Invalid SFO key offset')
        end = data.find(b'\0', start, values)
        if end < 0:
            raise ValueError('Unterminated SFO key')
        key = data[start:end]
        if not key or key in seen:
            raise ValueError('Duplicate or empty SFO key')
        seen.add(key)
        start = values + value_offset
        if size > capacity or start > len(data) or capacity > len(data) - start:
            raise ValueError('Invalid SFO value bounds')
        if key == b'DISC_ID':
            value = data[start:start + size]
            if fmt != 0x0204 or size < 2 or value[-1:] != b'\0' or b'\0' in value[:-1]:
                raise ValueError('Invalid SFO DISC_ID entry')
            try:
                result = value[:-1].decode('ascii')
            except UnicodeDecodeError as exc:
                raise ValueError('Invalid SFO DISC_ID encoding') from exc
    if result is None:
        raise ValueError('SFO does not identify a disc')
    return result


def make_manifest(game_iso):
    with open_regular(game_iso) as source:
        before = snapshot(source)
        image = ISO9660(source)
        sfo = image.files['PSP_GAME/PARAM.SFO']
        umd = image.files['UMD_DATA.BIN']
        executable = image.files['PSP_GAME/SYSDIR/EBOOT.BIN']
        asset = image.files['PSP_GAME/USRDIR/PSPASSET.DAT']
        if sfo.size > SMALL_CHECK or umd.size > SMALL_CHECK:
            raise ValueError('SFO or UMD metadata exceeds 64 KiB')
        disc_id = sfo_disc_id(image.read(sfo.offset, sfo.size))
        if disc_id != DISC_ID:
            raise ValueError(f'Wrong game edition: {disc_id}; expected {DISC_ID}')
        if executable.size < 4 or image.read(executable.offset, 4) not in (b'~PSP', b'\x7fELF'):
            raise ValueError('EBOOT.BIN is neither a PSP executable nor an ELF')
        umd_data = image.read(umd.offset, umd.size)
        if not umd_data.startswith((b'ULUS-10021', b'ULUS10021')):
            raise ValueError('UMD_DATA.BIN does not identify ULUS10021')
        ranges = [('iso-volume-descriptors', image.descriptors), ('param-sfo', sfo),
                  ('eboot', Extent(executable.offset, min(executable.size, SMALL_CHECK), False)),
                  ('umd-data', umd), ('pspasset', Extent(asset.offset, min(asset.size, SMALL_CHECK), False))]
        ranges.extend((f'iso-directory-{n}', d) for n, d in enumerate(image.directories))
        checks = [{'name': name, 'offset': extent.offset, 'size': extent.size,
                   'sha256': hashlib.sha256(image.read(extent.offset, extent.size)).hexdigest()}
                  for name, extent in ranges]
        if sum(c['size'] for c in checks) > MAX_CHECK_BYTES:
            raise ValueError('Boot checks exceed safe total size')
        source.seek(0)
        full_hash = stream_hash(source)
        if snapshot(source) != before:
            raise ValueError('ISO changed while being validated')
    return {'schema': 2, 'disc_id': disc_id,
            'iso': {'path': 'game.iso', 'size': image.size, 'sha256': full_hash},
            'boot_checks': checks}


def verify(game_iso, manifest):
    actual = make_manifest(game_iso)
    if actual != manifest:
        raise ValueError('ISO content does not match the schema-2 SHA-256 manifest')
    return actual


def load_manifest(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate manifest key: {key}')
            result[key] = value
        return result
    with open_regular(path) as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Manifest is too large')
    return json.loads(data, object_pairs_hook=unique_pairs)


def write_manifest(path, manifest):
    with Path(path).open('x', encoding='utf-8') as out:
        out.write(json.dumps(manifest, indent=2) + '\n')


def copy_verified(game_iso, destination, manifest):
    """Exclusively create game.iso, comparing its streamed bytes to the manifest."""
    with open_regular(game_iso) as source, Path(destination).open('xb') as target:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
            size += len(chunk)
            if size > manifest['iso']['size']:
                raise ValueError('ISO changed during copying')
            digest.update(chunk)
            target.write(chunk)
        if size != manifest['iso']['size'] or digest.hexdigest() != manifest['iso']['sha256']:
            raise ValueError('ISO changed during copying')
    verify(destination, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-iso', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--copy-to', type=Path, help='New private MC3Content directory; refuses overwrite')
    args = parser.parse_args()
    manifest = verify(args.game_iso, load_manifest(args.manifest)) if args.verify else make_manifest(args.game_iso)
    if not args.verify:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        write_manifest(args.manifest, manifest)
    if args.copy_to:
        # mkdir refuses existing directories as well as dangling symlinks.
        args.copy_to.mkdir(parents=True, exist_ok=False)
        try:
            copy_verified(args.game_iso, args.copy_to / 'game.iso', manifest)
            write_manifest(args.copy_to / 'manifest.json', manifest)
        except BaseException:
            # Keep failed copies unmistakable; never claim a partial content folder is verified.
            raise
    print(f'Verified ISO: {manifest["iso"]["size"]:,} bytes, {manifest["disc_id"]}, {len(manifest["boot_checks"])} boot checks.')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f'Content check failed: {exc}')
