# mc3

MC3 direct-launch iOS source kit, built on official PPSSPP v1.20.4 at commit `fa50bb1976065c4f8b1b47af227d367fe9771555`.

This runs the PSP game through PPSSPP internally. It is not a native recompile of Midnight Club 3. This public repository and its CI artifacts contain no Midnight Club 3 game data, saves, signing credentials, or private archives.

## Intended behavior

- Separate app named **Midnight Club 3**, bundle ID `local.mc3.directboot`.
- Starts the fixed bundled `MC3Content/game.iso` automatically without PPSSPP's game chooser.
- Missing/incomplete ISO or invalid boot data shows a game-specific recovery screen.
- Keeps PPSSPP touch/controller controls, pause/settings, audio/video, saves, and save states.
- Saves/configuration stay in the app's writable Documents sandbox. Back up saves before replacing an installation; deleting the app removes its sandbox.
- Incoming game links, switch-UMD, remote disc browsing, and developer game-selection paths are disabled.
- Uses the IR interpreter and disables startup JIT/ptrace attempts. Signing alone does not provide JIT or establish playable performance.
- Retains PPSSPP's original icon, credits, and license notices. This is an unofficial build.

An ISO is required because MC3's raw-sector read-ahead can cross file boundaries. PPSSPP's extracted-directory virtual disc cannot satisfy every such read. A reconstructed ISO must still be validated and tested; reconstruction does not establish the original retail sector layout.

## Build and test status

The workflow builds an unsigned, game-free iPhone arm64 app shell. This shell is not a playable game. Compilation does not establish signing, installation, gameplay, controls, saving, graphics/audio, or acceptable frame rate. Check the run for the exact commit before using any artifact.

Run the source-only tooling checks:

```sh
python3 -m unittest discover -s tests -v
bash -n tools/build-shell-macos.sh
bash -n tools/build-macos.sh
```

## Route A: private Mac build

Prerequisites: macOS, full Xcode selected as the active developer directory, iPhoneOS SDK, Git, CMake, Python 3, and `ditto`. A fresh output directory and enough disk space for PPSSPP's recursive dependencies are required. The scripts do not install software, accept Apple agreements, create credentials, or start paid services. The app targets physical arm64 iPhones/iPads on iOS 13 or later, not the simulator.

```sh
bash tools/build-macos.sh /absolute/path/to/game.iso /absolute/path/to/new-output
```

The script builds the unsigned content-free shell, then privately packages the verified USA ULUS10021 ISO. Output is `MC3-unsigned.ipa` plus its SHA-256 sidecar. Legitimate signing and device testing are still required.

## Route B: source-only CI and private ISO bundling

`.github/workflows/build-ios-shell.yml` runs on relevant pushes to `main` and can be started manually. It uses the standard `macos-15` GitHub-hosted runner and its installed Xcode 16.4. GitHub documents free standard-runner use for public repositories: https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job

1. Keep only code and build instructions in this public repository.
2. The workflow builds and checks an unsigned arm64 iPhone shell with no game content. It archives the exact patched PPSSPP source, recursive submodules, build kit, and revision manifest beside the shell. Combined artifact uploads are capped at 450 MB and retained for one day. No larger runners or paid-service settings are used.
3. Download the successful `MC3-shell-unsigned` artifact. Its shell has content schema 2, boot mode `iso`, and bundle version `12004.2`. Earlier directory-launch shells are incompatible.
4. On a private Python 3 computer, add your verified ISO:

```sh
python3 tools/bundle-private-game.py \
  --shell-ipa /path/to/MC3-shell-unsigned.ipa \
  --game-iso /private/path/to/game.iso \
  --output /private/path/to/MC3-unsigned.ipa
```

The bundler never uploads data. It validates the ISO9660 structure and game identity, hashes the whole ISO, checks streamed bytes while packaging, preserves the app executable/symlink metadata, and checks ZIP CRCs. It refuses incompatible, signed, content-filled, or unsafe shells and existing output files.

5. Sign the completed private IPA using an authorized signing method/profile. The signer must sign the app executable and embedded MoltenVK library and preserve the game data. This kit does not use jailbreak/private entitlements or signature bypasses. Uploading the finished IPA to a signing service transmits the game to that service; do so only with the necessary rights and explicit authorization.

## Integrity limits

The packaging tool validates the complete ISO SHA-256. Runtime preflight checks the ISO size and bounded SHA-256 ranges covering volume descriptors, directory layout, PARAM.SFO, boot executable prefix, UMD data, and the asset archive prefix. It does not hash the entire ISO at every launch. Same-size changes outside those ranges are not guaranteed to be caught before gameplay. Arbitrary emulator crashes or corrupt in-game assets cannot be guaranteed to produce the recovery screen.

No game executable, firmware, key, or game archive is provided here. Synthetic test fixtures contain no real game bytes.

## Physical-device acceptance checklist

Record iPhone model, iOS version, signing method, app version, and CPU/GPU backend.

- Cold launch boots MC3 directly, without a game chooser or unexpected permission prompt.
- Reach gameplay and drive a race. Check frame rate, sound, input lag, heat, and crashes. IR interpretation may be too slow.
- Test touch controls, controller if available, pause/resume, reset, and stop/restart.
- Save in the game, terminate normally, cold relaunch, and load the save. Test save states separately.
- Test background/foreground, audio interruptions, rotation, and repeated relaunch.
- Upgrade with the same bundle ID and confirm saves survive. Do not uninstall for this test.
- The content-free shell displays a readable missing-content screen. A corrupt-boot test build displays recovery, never another game picker.
- External game links and controller developer shortcuts cannot select other games.
- Test the final legitimately signed IPA, including its embedded library.

## Source and notices

Keep the matching source archive and build kit beside the resulting binary. The app includes source revision metadata and license/notice files. PPSSPP's GPL-2.0-or-later license and dependency terms continue to apply. Game redistribution rights are separate and are not provided by this kit.
