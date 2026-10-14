#!/bin/bash
# Private local build. Does not upload anything or create signing credentials.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo 'Usage: bash tools/build-shell-macos.sh /absolute/new/output-dir' >&2
  exit 2
fi
[[ $(uname -s) == Darwin ]] || { echo 'Requires macOS with full Xcode and the iPhoneOS SDK.' >&2; exit 1; }
for tool in git cmake python3 xcodebuild xcrun ditto; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
xcrun --sdk iphoneos --show-sdk-path >/dev/null
BASE=$(cd "$(dirname "$0")/.." && pwd)
[[ $1 == /* ]] || { echo 'Output directory must be absolute.' >&2; exit 1; }
[[ ! -e $1 ]] || { echo 'Use a new output directory so no prior build or saves can be overwritten.' >&2; exit 1; }
OUT=$1
mkdir -p "$OUT"
PIN=fa50bb1976065c4f8b1b47af227d367fe9771555
git -c core.hooksPath=/dev/null clone --branch v1.20.4 --depth 1 --no-recurse-submodules https://github.com/hrydgard/ppsspp.git "$OUT/ppsspp"
cd "$OUT/ppsspp"
[[ $(git rev-parse HEAD) == "$PIN" ]] || { echo 'Upstream tag changed; pinned commit mismatch.' >&2; exit 1; }
git apply --check "$BASE/mc3-directboot.patch"
git apply "$BASE/mc3-directboot.patch"
git -c core.hooksPath=/dev/null submodule update --init --recursive
mkdir -p build-ios
printf 'const char *PPSSPP_GIT_VERSION = "v1.20.4";\n#define PPSSPP_GIT_VERSION_NO_UPDATE 1\n' > build-ios/git-version.cpp
cp build-ios/git-version.cpp git-version.cpp
cmake -S . -B build-ios -G Xcode \
  -DCMAKE_TOOLCHAIN_FILE="$OUT/ppsspp/cmake/Toolchains/ios.cmake" \
  -DPPSSPP_SINGLE_GAME=ON
xcodebuild -project build-ios/PPSSPP.xcodeproj -scheme PPSSPP -sdk iphoneos \
  -configuration Release clean build CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=NO IPHONEOS_DEPLOYMENT_TARGET=13.0
APP="$OUT/staging/Payload/MC3.app"
mkdir -p "$(dirname "$APP")"
ditto build-ios/Release-iphoneos/PPSSPP.app "$APP"
mkdir -p "$APP/Frameworks"
ditto ext/vulkan/iOS/Frameworks/libMoltenVK.dylib "$APP/Frameworks/libMoltenVK.dylib"
python3 - "$APP/Info.plist" <<'PY'
import plistlib, sys
from pathlib import Path
p = Path(sys.argv[1]); info = plistlib.loads(p.read_bytes())
info.update(CFBundleDisplayName='Midnight Club 3', CFBundleName='MC3',
            CFBundleIdentifier='local.mc3.directboot', CFBundleShortVersionString='1.20.4',
            CFBundleVersion='12004.2', MC3ContentSchema=2, MC3BootMode='iso', UISupportsDocumentBrowser=False,
            UIFileSharingEnabled=True)
for key in ('CFBundleURLTypes', 'CFBundleDocumentTypes', 'UTExportedTypeDeclarations'):
    info.pop(key, None)
p.write_bytes(plistlib.dumps(info))
PY
cp "$BASE/COPYING" "$APP/PPSSPP-COPYING.txt"
cp "$BASE/UPSTREAM.txt" "$APP/PPSSPP-SOURCE.txt"
python3 - "$OUT/ppsspp" "$APP/OpenSourceNotices" <<'PY_NOTICES'
from pathlib import Path
import re, shutil, sys
root, dest = map(Path, sys.argv[1:])
for path in root.rglob('*'):
    rel = path.relative_to(root)
    if any(part == '.git' or part.startswith('build-') for part in rel.parts):
        continue
    if path.is_file() and re.match(r'(?i)^(licen[cs]e|copying|notice|copyright)([._-].*)?$', path.name):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
PY_NOTICES
{ git rev-parse HEAD; git submodule status --recursive; } > "$APP/PPSSPP-SOURCE-REVISIONS.txt"
cp "$BASE/mc3-directboot.patch" "$APP/MC3-SOURCE-PATCH.txt"
# Verify the staged build identity and real iPhone device architecture before upload.
xcrun lipo "$APP/PPSSPP" -verify_arch arm64
xcrun lipo "$APP/Frameworks/libMoltenVK.dylib" -verify_arch arm64
file "$APP/PPSSPP" "$APP/Frameworks/libMoltenVK.dylib"
python3 - "$APP" <<'PY_VERIFY'
from pathlib import Path
import plistlib, sys
app = Path(sys.argv[1])
info = plistlib.loads((app / 'Info.plist').read_bytes())
assert info['CFBundleIdentifier'] == 'local.mc3.directboot', 'Unexpected app ID'
assert info['CFBundleExecutable'] == 'PPSSPP', 'Unexpected executable'
assert info['MC3ContentSchema'] == 2 and info['MC3BootMode'] == 'iso', 'Unexpected content format'
assert 'iPhoneOS' in info['CFBundleSupportedPlatforms'], 'Not an iPhone device build'
assert not (app / 'MC3Content').exists(), 'Private game content must never enter CI'
assert not (app / '_CodeSignature').exists(), 'Expected an unsigned shell'
assert not (app / 'embedded.mobileprovision').exists(), 'Signing profile must not enter CI'
print('Verified content-free, unsigned arm64 iPhone app shell identity.')
PY_VERIFY
# No ldid, private entitlements, paid services, or signature bypasses.
(cd "$OUT/staging" && ditto -c -k --norsrc --noextattr --keepParent Payload "$OUT/MC3-shell-unsigned.ipa")
shasum -a 256 "$OUT/MC3-shell-unsigned.ipa" > "$OUT/MC3-shell-unsigned.ipa.sha256"
echo "Created $OUT/MC3-shell-unsigned.ipa. This is NOT installable until properly signed."
echo 'This shell intentionally has no game content. Use bundle-private-game.py locally to add your verified game ISO.'
echo 'No iPhone gameplay, performance, entitlement, or signing compatibility is established by this build.'
