"""All image bytes and executables here are synthetic; no game assets are used."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import content
spec = importlib.util.spec_from_file_location('bundler', TOOLS / 'bundle-private-game.py')
bundler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundler)
BLOCK = content.BLOCK


def n16(value): return struct.pack('<H', value) + struct.pack('>H', value)
def n32(value): return struct.pack('<I', value) + struct.pack('>I', value)


def sfo(disc_id='ULUS10021'):
    key, value = b'DISC_ID\0', disc_id.encode() + b'\0'
    return (struct.pack('<5I', 0x46535000, 0x101, 36, 36 + len(key), 1)
            + struct.pack('<HHIII', 0, 0x0204, len(value), len(value), 0) + key + value)


def record(name, sector, size, directory=False):
    data = bytearray(33 + len(name) + (len(name) % 2 == 0))
    data[0] = len(data)
    data[2:10] = n32(sector)
    data[10:18] = n32(size)
    data[18:25] = bytes((126, 1, 1, 0, 0, 0, 0))
    data[25] = 2 if directory else 0
    data[28:32] = n16(1)
    data[32] = len(name)
    data[33:33 + len(name)] = name
    return data


class SyntheticISO:
    def __init__(self, path, replacements=None, remove=(), extra=None, enhanced=True):
        self.path = Path(path)
        files = {'UMD_DATA.BIN': b'ULUS-10021|SYNTHETIC-TEST-ONLY',
                 'PSP_GAME/PARAM.SFO': sfo(),
                 'PSP_GAME/SYSDIR/EBOOT.BIN': b'~PSP' + b'SYNTHETIC-EXECUTABLE' * 30,
                 'PSP_GAME/USRDIR/pspasset.dat': b'SYNTHETIC-ASSET' * 6000}
        files.update(replacements or {})
        files.update(extra or {})
        for name in remove:
            files.pop(name)
        directories = {''}
        for name in files:
            parent = str(Path(name).parent)
            while parent != '.':
                directories.add(parent)
                parent = str(Path(parent).parent)
        ordered = sorted(directories, key=lambda n: (n.count('/') + bool(n), n))
        self.directory_sectors = {name: 21 + n for n, name in enumerate(ordered)}
        next_sector = 21 + len(ordered)
        self.files = {}
        for name, data in sorted(files.items()):
            self.files[name] = (next_sector * BLOCK, len(data))
            next_sector += (len(data) + BLOCK - 1) // BLOCK
        self.data = bytearray(max(next_sector, 32) * BLOCK)
        self.records = {}
        for directory in ordered:
            sector = self.directory_sectors[directory]
            parent = str(Path(directory).parent) if directory else ''
            if parent == '.': parent = ''
            contents = record(b'\0', sector, BLOCK, True)
            contents += record(b'\1', self.directory_sectors[parent], BLOCK, True)
            children = []
            for name in ordered:
                if name and (str(Path(name).parent).replace('.', '') == directory):
                    children.append((name, record(Path(name).name.encode(), self.directory_sectors[name], BLOCK, True)))
            for name, (offset, size) in self.files.items():
                if str(Path(name).parent).replace('.', '') == directory:
                    children.append((name, record((Path(name).name + ';1').encode(), offset // BLOCK, size)))
            for name, entry in sorted(children):
                self.records[name] = sector * BLOCK + len(contents)
                contents += entry
            assert len(contents) < BLOCK
            self.data[sector * BLOCK:sector * BLOCK + len(contents)] = contents
        for name, payload in files.items():
            offset, size = self.files[name]
            self.data[offset:offset + size] = payload
        path_tables = []
        for endian in ('<', '>'):
            table = bytearray()
            for name in ordered:
                identifier = b'\0' if not name else Path(name).name.encode()
                parent = str(Path(name).parent) if name else ''
                if parent == '.': parent = ''
                table += bytes((len(identifier), 0)) + struct.pack(endian + 'IH', self.directory_sectors[name], ordered.index(parent) + 1) + identifier
                if len(identifier) % 2: table += b'\0'
            path_tables.append(table)
        pvd = bytearray(BLOCK)
        pvd[:7] = b'\x01CD001\x01'
        pvd[8:40] = b'SYNTHETIC TEST'.ljust(32, b' ')
        pvd[40:72] = b'MC3_SYNTHETIC_FIXTURE'.ljust(32, b' ')
        pvd[80:88] = n32(len(self.data) // BLOCK)
        pvd[120:124] = n16(1)
        pvd[124:128] = n16(1)
        pvd[128:132] = n16(BLOCK)
        pvd[132:140] = n32(len(path_tables[0]))
        pvd[140:144] = struct.pack('<I', 19)
        pvd[148:152] = struct.pack('>I', 20)
        pvd[156:190] = record(b'\0', self.directory_sectors[''], BLOCK, True)
        pvd[881] = 1
        self.data[16 * BLOCK:17 * BLOCK] = pvd
        terminator_sector = 17
        if enhanced:
            descriptor = bytearray(pvd)
            descriptor[0], descriptor[6] = 2, 2
            self.data[17 * BLOCK:18 * BLOCK] = descriptor
            terminator_sector = 18
        self.data[terminator_sector * BLOCK:terminator_sector * BLOCK + 7] = b'\xffCD001\x01'
        for sector, table in zip((19, 20), path_tables):
            self.data[sector * BLOCK:sector * BLOCK + len(table)] = table
        self.save()

    def save(self): self.path.write_bytes(self.data)
    def patch(self, offset, data):
        self.data[offset:offset + len(data)] = data
        self.save()


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.iso = self.root / 'game.iso'
        self.fixture = SyntheticISO(self.iso)
    def tearDown(self): self.tmp.cleanup()
    def invalid(self, regex='.'):
        with self.assertRaisesRegex(ValueError, regex): content.make_manifest(self.iso)
    def shell(self, extras=(), info_changes=None, executable=None):
        path = self.root / 'shell.ipa'
        info = {'CFBundleIdentifier': 'local.mc3.directboot', 'CFBundleExecutable': 'PPSSPP',
                'CFBundleVersion': '12004.2', 'MC3ContentSchema': 2, 'MC3BootMode': 'iso'}
        info.update(info_changes or {})
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.comment = b'Synthetic test comment'
                z.writestr(bundler.PREFIX + 'Info.plist', plistlib.dumps(info))
                entry = zipfile.ZipInfo(bundler.PREFIX + 'PPSSPP')
                entry.create_system = 3
                entry.external_attr = 0o100755 << 16
                entry.comment = b'Executable test metadata'
                z.writestr(entry, executable or b'SYNTHETIC TEST DATA, NOT AN EXECUTABLE')
                for value in extras:
                    if isinstance(value, tuple): z.writestr(*value)
                    else: z.writestr(value, b'no')
        return path
    def package(self, shell=None):
        return bundler.package(shell or self.shell(), self.iso, self.root / 'out.ipa')


class ContentTests(BaseTest):
    def test_exact_contract_and_full_hash(self):
        manifest = content.make_manifest(self.iso)
        self.assertEqual(set(manifest), {'schema', 'disc_id', 'iso', 'boot_checks'})
        self.assertEqual(manifest['schema'], 2)
        self.assertEqual(manifest['disc_id'], 'ULUS10021')
        self.assertEqual(manifest['iso'], {'path': 'game.iso', 'size': len(self.fixture.data), 'sha256': hashlib.sha256(self.fixture.data).hexdigest()})
        checks = manifest['boot_checks']
        self.assertEqual([c['name'] for c in checks[:5]], ['iso-volume-descriptors', 'param-sfo', 'eboot', 'umd-data', 'pspasset'])
        self.assertEqual(len(checks), 9)
        for check in checks:
            self.assertEqual(set(check), {'name', 'offset', 'size', 'sha256'})
            data = self.fixture.data[check['offset']:check['offset'] + check['size']]
            self.assertEqual(hashlib.sha256(data).hexdigest(), check['sha256'])
        self.assertEqual(content.verify(self.iso, manifest), manifest)
    def test_standard_primary_only(self):
        SyntheticISO(self.iso, enhanced=False)
        self.assertEqual(content.make_manifest(self.iso)['boot_checks'][0]['size'], BLOCK * 2)
    def test_prefix_hashes_bounded(self):
        SyntheticISO(self.iso, replacements={'PSP_GAME/SYSDIR/EBOOT.BIN': b'\x7fELF' + b'E' * 100000})
        checks = {c['name']: c for c in content.make_manifest(self.iso)['boot_checks']}
        self.assertEqual(checks['eboot']['size'], 65536)
        self.assertEqual(checks['pspasset']['size'], 65536)
    def test_full_hash_detects_same_size_change_outside_boot_prefix(self):
        manifest = content.make_manifest(self.iso)
        offset, size = self.fixture.files['PSP_GAME/USRDIR/pspasset.dat']
        self.fixture.patch(offset + size - 1, b'X')
        self.assertEqual(content.make_manifest(self.iso)['boot_checks'], manifest['boot_checks'])
        with self.assertRaisesRegex(ValueError, 'manifest'): content.verify(self.iso, manifest)
    def test_every_required_file_missing(self):
        for name in self.fixture.files:
            with self.subTest(name=name):
                SyntheticISO(self.iso, remove=(name,))
                self.invalid('Missing')
    def test_empty_required_file(self):
        SyntheticISO(self.iso, replacements={'PSP_GAME/USRDIR/pspasset.dat': b''})
        self.invalid('empty')
    def test_wrong_disc(self):
        SyntheticISO(self.iso, replacements={'PSP_GAME/PARAM.SFO': sfo('ULES99999')})
        self.invalid('Wrong game')
    def test_wrong_umd(self):
        SyntheticISO(self.iso, replacements={'UMD_DATA.BIN': b'ULES-99999'})
        self.invalid('UMD_DATA')
    def test_bad_executable(self):
        SyntheticISO(self.iso, replacements={'PSP_GAME/SYSDIR/EBOOT.BIN': b'NOPE'})
        self.invalid('neither')
    def test_invalid_sfo(self):
        SyntheticISO(self.iso, replacements={'PSP_GAME/PARAM.SFO': b'invalid'})
        self.invalid('SFO')
    def test_sfo_bounds_and_format(self):
        cases = [(8, struct.pack('<I', 0)), (12, struct.pack('<I', 500)), (16, struct.pack('<I', 1025)),
                 (20, struct.pack('<H', 50)), (22, struct.pack('<H', 4)), (24, struct.pack('<I', 20)),
                 (28, struct.pack('<I', 500)), (32, struct.pack('<I', 500))]
        for offset, replacement in cases:
            with self.subTest(offset=offset):
                data = bytearray(sfo()); data[offset:offset + len(replacement)] = replacement
                SyntheticISO(self.iso, replacements={'PSP_GAME/PARAM.SFO': bytes(data)})
                self.invalid('SFO')
    def test_oversized_metadata(self):
        SyntheticISO(self.iso, replacements={'UMD_DATA.BIN': b'ULUS-10021' + b'X' * 65536})
        self.invalid('64 KiB')
    def test_wrong_iso_magic(self):
        self.fixture.patch(16 * BLOCK + 1, b'WRONG')
        self.invalid('descriptor')
    def test_declared_size_mismatch(self):
        self.fixture.patch(16 * BLOCK + 80, n32(32))
        self.invalid('volume size')
    def test_endian_mismatch(self):
        self.fixture.patch(16 * BLOCK + 84, struct.pack('>I', 999))
        self.invalid('endian')
    def test_wrong_sector_size(self):
        self.fixture.patch(16 * BLOCK + 128, n16(512))
        self.invalid('2048')
    def test_multivolume(self):
        self.fixture.patch(16 * BLOCK + 120, n16(2))
        self.invalid('single-volume')
    def test_no_primary(self):
        self.fixture.patch(16 * BLOCK, b'\x02')
        self.invalid('primary')
    def test_duplicate_primary(self):
        self.fixture.patch(17 * BLOCK, b'\x01')
        self.invalid('primary')
    def test_extent_outside_image(self):
        self.fixture.patch(self.fixture.records['PSP_GAME/PARAM.SFO'] + 2, n32(999999))
        self.invalid('outside')
    def test_overlapping_extents(self):
        asset = self.fixture.records['PSP_GAME/USRDIR/pspasset.dat']
        self.fixture.patch(asset + 2, n32(self.fixture.files['PSP_GAME/SYSDIR/EBOOT.BIN'][0] // BLOCK))
        self.invalid('Overlapping')
    def test_multiextent_interleave_and_extended_attribute_rejected(self):
        for offset, value in ((25, 0x80), (26, 1), (27, 1), (1, 1)):
            with self.subTest(offset=offset):
                fixture = SyntheticISO(self.iso)
                fixture.patch(fixture.records['PSP_GAME/PARAM.SFO'] + offset, bytes((value,)))
                self.invalid('Unsupported')
    def test_duplicate_casefold_names(self):
        SyntheticISO(self.iso, extra={'PSP_GAME/dummy': b'a', 'PSP_GAME/DUMMY': b'b'})
        self.invalid('Duplicate')
    def test_extra_root_file(self):
        SyntheticISO(self.iso, extra={'readme.txt': b'extra'})
        self.invalid('top-level')
    def test_nested_archive(self):
        for extension in content.ARCHIVE_SUFFIXES:
            with self.subTest(extension=extension):
                SyntheticISO(self.iso, extra={'PSP_GAME/copy' + extension: b'extra'})
                self.invalid('Nested archive')
    def test_updater(self):
        SyntheticISO(self.iso, extra={'PSP_GAME/UPDATE/EBOOT.PBP': b'firmware'})
        self.invalid('updater')
    def test_missing_self_directory_record(self):
        offset = self.fixture.directory_sectors['PSP_GAME'] * BLOCK
        self.fixture.patch(offset + 33, b'Q')
        self.invalid('self or parent')
    def test_invalid_directory_padding(self):
        offset = self.fixture.directory_sectors[''] * BLOCK + BLOCK - 1
        self.fixture.patch(offset, b'X')
        self.invalid('padding')
    def test_unsafe_iso_identifier(self):
        offset = self.fixture.records['PSP_GAME/PARAM.SFO'] + 33
        self.fixture.patch(offset, b'/')
        self.invalid('Unsafe')
    def test_small_unaligned_and_too_large_images(self):
        for size in (123, 65537, content.MAX_ISO_SIZE + BLOCK):
            with self.subTest(size=size):
                with self.iso.open('wb') as output: output.truncate(size)
                self.invalid('ISO size')
    def test_symlink_input(self):
        target = self.root / 'link.iso'; target.symlink_to(self.iso)
        with self.assertRaisesRegex(ValueError, 'Symlinks'): content.make_manifest(target)
    def test_nonregular_input(self):
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular'): content.make_manifest(fifo)
    def test_manifest_no_overwrite(self):
        target = self.root / 'manifest.json'; target.write_text('original')
        with self.assertRaises(FileExistsError): content.write_manifest(target, {})
        self.assertEqual(target.read_text(), 'original')
    def test_duplicate_manifest_key(self):
        target = self.root / 'manifest.json'; target.write_text('{"schema":2,"schema":1}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'): content.load_manifest(target)
    def test_copy_and_cli(self):
        manifest = self.root / 'manifest.json'; directory = self.root / 'MC3Content'
        result = subprocess.run([sys.executable, str(TOOLS / 'content.py'), '--game-iso', str(self.iso), '--manifest', str(manifest), '--copy-to', str(directory)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in directory.iterdir()}, {'game.iso', 'manifest.json'})
        self.assertEqual((directory / 'game.iso').read_bytes(), self.iso.read_bytes())
        result = subprocess.run([sys.executable, str(TOOLS / 'content.py'), '--game-iso', str(self.iso), '--manifest', str(manifest), '--verify'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class BundleTests(BaseTest):
    def test_bundle_preserves_mode_symlinks_metadata_and_iso(self):
        link = zipfile.ZipInfo(bundler.PREFIX + 'Frameworks/Example.framework/Example')
        link.create_system = 3; link.external_attr = 0o120777 << 16
        target = bundler.PREFIX + 'Frameworks/Example.framework/Versions/A/Example'
        shell = self.shell([(link, b'Versions/A/Example'), (target, b'synthetic framework')])
        manifest = self.package(shell)
        out = self.root / 'out.ipa'
        with zipfile.ZipFile(out) as z:
            executable = z.getinfo(bundler.PREFIX + 'PPSSPP')
            self.assertEqual(executable.external_attr >> 16, 0o100755)
            self.assertEqual(executable.comment, b'Executable test metadata')
            self.assertEqual(z.getinfo(link.filename).external_attr >> 16, 0o120777)
            self.assertEqual(z.read(link.filename), b'Versions/A/Example')
            self.assertEqual(z.comment, b'Synthetic test comment')
            self.assertEqual(z.read(bundler.PREFIX + 'MC3Content/game.iso'), self.iso.read_bytes())
            self.assertEqual(json.loads(z.read(bundler.PREFIX + 'MC3Content/manifest.json')), manifest)
            self.assertIsNone(z.testzip())
            private = [n for n in z.namelist() if n.startswith(bundler.PREFIX + 'MC3Content/')]
            self.assertEqual(len(private), 2)
        self.assertEqual(Path(str(out) + '.sha256').read_text(), f'{content.sha256(out)}  out.ipa\n')
    def test_output_and_sidecar_overwrite_refused(self):
        shell = self.shell()
        for name in ('out.ipa', 'out.ipa.sha256'):
            with self.subTest(name=name):
                output = self.root / name; output.write_bytes(b'original')
                with self.assertRaisesRegex(ValueError, 'already exists'): self.package(shell)
                self.assertEqual(output.read_bytes(), b'original'); output.unlink()
    def test_dangling_output_symlink(self):
        (self.root / 'out.ipa').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'already exists'): self.package()
    def test_signed_or_contentful_shell_refused(self):
        for suffix in ('_CodeSignature/CodeResources', '_codesignature', 'embedded.mobileprovision', 'MC3Content', 'mc3content/game.iso'):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, 'unsigned'): self.package(self.shell([bundler.PREFIX + suffix]))
    def test_embedded_macho_signature(self):
        header = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 16, 0, 0)
        binary = header + struct.pack('<4I', 0x1d, 16, 48, 8) + b'SIGNED!!'
        with self.assertRaisesRegex(ValueError, 'code signature'): self.package(self.shell(executable=binary))
    def test_unsigned_macho(self):
        binary = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 0, 0, 0, 0)
        self.package(self.shell(executable=binary))
    def test_unsigned_32bit_macho_header_is_bounded(self):
        binary = struct.pack('<7I', 0xfeedface, 12, 0, 2, 0, 0, 0)
        self.package(self.shell(executable=binary))
    def test_sibling_app_refused(self):
        with self.assertRaisesRegex(ValueError, 'sibling'): self.package(self.shell(['Payload/Another.app/file']))
    def test_traversal_and_ambiguous_paths_refused(self):
        for name in ('Payload/../escape', '/Payload/MC3.app/a', 'Payload//MC3.app/a', 'Payload/MC3.app/./a', 'Payload/MC3.app/a\\b', 'Payload/MC3.app/a:', 'Payload/MC3.app/a.'): 
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Unsafe'): self.package(self.shell([name]))
    def test_duplicate_archive_paths(self):
        for name in (bundler.PREFIX + 'PPSSPP', bundler.PREFIX + 'ppsspp'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Duplicate'): self.package(self.shell([name]))
    def test_old_shell_and_wrong_identity(self):
        for change in ({'MC3ContentSchema': 1}, {'MC3ContentSchema': True}, {'MC3BootMode': 'directory'}, {'CFBundleVersion': '12004.1'}, {'CFBundleIdentifier': 'wrong'}, {'CFBundleExecutable': 'wrong'}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.package(self.shell(info_changes=change))
    def test_symlink_escape(self):
        link = zipfile.ZipInfo(bundler.PREFIX + 'Frameworks/link')
        link.create_system = 3; link.external_attr = 0o120777 << 16
        for target in ('../../../outside', '/absolute', '../../MC3Content', '../MC3Content/game.iso'):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, 'symlink'): self.package(self.shell([(link, target)]))
    def test_entry_below_symlink(self):
        link = zipfile.ZipInfo(bundler.PREFIX + 'Frameworks/link')
        link.create_system = 3; link.external_attr = 0o120777 << 16
        with self.assertRaisesRegex(ValueError, 'parent directory'):
            self.package(self.shell([(link, b'Other'), (link.filename + '/file', b'a')]))
    def test_special_node(self):
        entry = zipfile.ZipInfo(bundler.PREFIX + 'device'); entry.create_system = 3; entry.external_attr = (stat.S_IFCHR | 0o600) << 16
        with self.assertRaisesRegex(ValueError, 'Special'): self.package(self.shell([(entry, b'')]))
    def test_changed_iso_during_packaging_is_cleaned_up(self):
        real = bundler.make_manifest
        def change(path):
            manifest = real(path)
            offset, size = self.fixture.files['PSP_GAME/USRDIR/pspasset.dat']
            self.fixture.patch(offset + size - 1, b'X')
            return manifest
        with mock.patch.object(bundler, 'make_manifest', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'changed during'): self.package()
        self.assertFalse((self.root / 'out.ipa').exists())
        self.assertFalse((self.root / 'out.ipa.sha256').exists())
    def test_crc_failure_is_cleaned_up(self):
        with mock.patch.object(zipfile.ZipFile, 'testzip', return_value='corrupt-member'):
            with self.assertRaisesRegex(ValueError, 'Corrupt'): self.package()
        self.assertFalse((self.root / 'out.ipa').exists())
    def test_extra_fields_preserved_without_stale_zip64(self):
        entry = zipfile.ZipInfo('test')
        entry.extra = struct.pack('<HH8s', 1, 8, b'12345678') + struct.pack('<HH3s', 0xbeef, 3, b'abc')
        self.assertEqual(bundler.copied_entry(entry).extra, struct.pack('<HH3s', 0xbeef, 3, b'abc'))
    def test_cli_uses_game_iso(self):
        output = self.root / 'out.ipa'
        result = subprocess.run([sys.executable, str(TOOLS / 'bundle-private-game.py'), '--shell-ipa', str(self.shell()), '--game-iso', str(self.iso), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())


if __name__ == '__main__': unittest.main()
