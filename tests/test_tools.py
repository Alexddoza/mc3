import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import content
spec = importlib.util.spec_from_file_location('bundler', TOOLS / 'bundle-private-game.py')
bundler = importlib.util.module_from_spec(spec); spec.loader.exec_module(bundler)

def fixture(root, disc_id='ULUS10021'):
    key = b'DISC_ID\0'; value = disc_id.encode() + b'\0'
    data = struct.pack('<5I', 0x46535000, 0x101, 36, 36 + len(key), 1)
    data += struct.pack('<HHIII', 0, 0x0204, len(value), len(value), 0) + key + value
    files = {'PSP_GAME/PARAM.SFO': data, 'PSP_GAME/SYSDIR/EBOOT.BIN': b'~PSP-test',
             'PSP_GAME/USRDIR/pspasset.dat': b'asset-bytes', 'UMD_DATA.BIN': b'ULUS-10021'}
    for name, payload in files.items():
        p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(payload)

class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'game'; fixture(self.root)
    def tearDown(self): self.tmp.cleanup()
    def test_valid_manifest(self):
        m = content.make_manifest(self.root)
        self.assertEqual(m['disc_id'], 'ULUS10021'); self.assertEqual(m['file_count'], 4)
        self.assertEqual(content.verify(self.root, m), m)
    def test_same_size_corruption(self):
        m = content.make_manifest(self.root)
        (self.root / 'PSP_GAME/USRDIR/pspasset.dat').write_bytes(b'asset-byteX')
        with self.assertRaisesRegex(ValueError, 'manifest'): content.verify(self.root, m)
    def test_missing_executable(self):
        (self.root / 'PSP_GAME/SYSDIR/EBOOT.BIN').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'): content.make_manifest(self.root)
    def test_wrong_disc(self):
        fixture(self.root, 'ULES99999')
        with self.assertRaisesRegex(ValueError, 'Wrong game'): content.make_manifest(self.root)
    def test_invalid_sfo(self):
        (self.root / 'PSP_GAME/PARAM.SFO').write_bytes(b'invalid')
        with self.assertRaisesRegex(ValueError, 'SFO'): content.make_manifest(self.root)
    def test_symlink(self):
        (self.root / 'PSP_GAME/link').symlink_to(self.root / 'UMD_DATA.BIN')
        with self.assertRaisesRegex(ValueError, 'Symlinks'): content.make_manifest(self.root)
    def test_nested_archive(self):
        (self.root / 'PSP_GAME/copy.zip').write_bytes(b'archive')
        with self.assertRaisesRegex(ValueError, 'Nested archive'): content.make_manifest(self.root)
    def test_firmware_updater(self):
        update = self.root / 'PSP_GAME/UPDATE'; update.mkdir(); (update / 'EBOOT.PBP').write_bytes(b'update')
        with self.assertRaisesRegex(ValueError, 'updater'): content.make_manifest(self.root)
    def shell(self, extras=()):
        path = Path(self.tmp.name) / 'shell.zip'
        with zipfile.ZipFile(path, 'w') as z:
            info = {'CFBundleIdentifier': 'local.mc3.directboot', 'CFBundleExecutable': 'PPSSPP'}
            z.writestr(bundler.PREFIX + 'Info.plist', plistlib.dumps(info))
            entry = zipfile.ZipInfo(bundler.PREFIX + 'PPSSPP'); entry.create_system=3; entry.external_attr=0o100755 << 16
            z.writestr(entry, b'SYNTHETIC TEST DATA, NOT AN EXECUTABLE')
            for name in extras: z.writestr(name, b'no')
        return path
    def test_bundle_preserves_executable_mode_and_game(self):
        out = Path(self.tmp.name) / 'output.zip'
        m = bundler.package(self.shell(), self.root, out)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.getinfo(bundler.PREFIX + 'PPSSPP').external_attr >> 16, 0o100755)
            self.assertEqual(json.loads(z.read(bundler.PREFIX + 'MC3Content/manifest.json')), m)
            self.assertIsNone(z.testzip())
        self.assertTrue(Path(str(out) + '.sha256').exists())
    def test_output_overwrite_refused(self):
        out = Path(self.tmp.name) / 'output.zip'; out.write_bytes(b'original')
        with self.assertRaisesRegex(ValueError, 'already exists'): bundler.package(self.shell(), self.root, out)
        self.assertEqual(out.read_bytes(), b'original')
    def test_signed_shell_refused(self):
        with self.assertRaisesRegex(ValueError, 'unsigned'):
            bundler.package(self.shell([bundler.PREFIX + '_CodeSignature/CodeResources']), self.root, Path(self.tmp.name) / 'output.zip')
    def test_sibling_app_refused(self):
        with self.assertRaisesRegex(ValueError, 'sibling'):
            bundler.package(self.shell(['Payload/Another.app/file']), self.root, Path(self.tmp.name) / 'output.zip')
    def test_traversal_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            bundler.package(self.shell(['Payload/../escape']), self.root, Path(self.tmp.name) / 'output.zip')

if __name__ == '__main__': unittest.main()
