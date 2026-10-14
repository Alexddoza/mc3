# mc3

MC3 direct-launch iOS source kit

This is a source/build handoff, not a compiled or tested iPhone app. It modifies official PPSSPP v1.20.4 at commit `fa50bb1976065c4f8b1b47af227d367fe9771555`. It runs the PSP game through PPSSPP internally. It is not a native recompile of Midnight Club 3.

## Intended app behavior

- A separate app named **Midnight Club 3**, bundle ID `local.mc3.directboot`.
- Launch resolves `MC3Content/game-root` in the current app bundle and boots it automatically, skipping the PPSSPP logo/game chooser.
- The extracted directory containing `PSP_GAME` and `UMD_DATA.BIN` is directly supported by PPSSPP; no ISO reconstruction is required.
- Missing/incomplete files and detected boot corruption show a game-specific error/recovery screen. No game chooser is used on that path or after stopping the game.
- PPSSPP touch controls, controller support, game saves, save states, audio/video, and pause/settings remain.
- Private game files are read from the app bundle; saves/configuration remain in the app's writable Documents sandbox. Installing over the same app ID should retain data, but deleting the app removes its sandbox. Back up saves before replacing an installation.
- Incoming file/deep links, switch-UMD, remote disc browsing, and developer-game-selection paths are disabled for this build. Internal EmuScreen construction is pinned to this game.
- This signing-compatible baseline deliberately disables PPSSPP's startup JIT/ptrace attempts and uses its IR interpreter. No promise of full speed is made. Signing via Signulous does not by itself establish JIT availability.
- The original PPSSPP icon and credits are retained; the app label is customized. This is an unofficial private build.

## Build status and scope

The repository contains source and build instructions only. GitHub Actions builds an unsigned, game-free iOS app shell. The shell is not a playable game and must not be presented as one. A successful compile does not establish codesigning, installation, physical-device controls, saves, graphics/audio, or performance.

Local Python-tool tests and shell syntax checks are reproducible with:

```sh
python3 -m unittest discover -s tests -v
bash -n tools/build-shell-macos.sh
bash -n tools/build-macos.sh
```

## Route A: one private Mac build

Prerequisites: a Mac, full Xcode selected as the active developer directory, the iPhoneOS SDK, Git, CMake, Python 3, and `ditto`. The script checks these; it does not install them, accept Apple agreements, create credentials, or start paid services. A full recursive PPSSPP source/dependency download and adequate disk space are required. The upstream toolchain targets arm64 physical devices, not an iPhone simulator.

From this kit:

```sh
bash tools/build-macos.sh /absolute/path/to/game-root /absolute/path/to/new-output
```

The output directory must be new. The script downloads the official pinned source and its recursive submodules, applies the patch, compiles an unsigned Release app using Xcode, packages the source-only shell, then privately inserts the game. Expected final output is `MC3-unsigned.ipa` with a SHA-256 sidecar. It still requires legitimate signing before installation.

A failed prerequisite/build step stops the script. Do not treat a partial output or source-only shell as the final working game.

## Route B: source-only Mac CI, private local game bundling

`.github/workflows/build-ios-shell.yml` runs on pushes to `main` and can be started manually. It uses the standard `macos-15` GitHub-hosted runner and its installed Xcode 16.4. GitHub documents standard runner usage as free for public repositories: https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job

1. Keep only source and build instructions in this public repository. Never commit game files, save data, signing credentials, or private archives.
2. The workflow tests the packaging tools, then builds the pinned unsigned app with no game files. It keeps `MC3-shell-unsigned.ipa` and the matching patched source/submodules for one day, with a strict 450 MB combined upload cap. This incomplete shell shows a missing-game screen if legitimately signed and installed.
3. Download the successful shell artifact. On Linux, macOS, or another Python 3 computer with your private game-root, run:

```sh
python3 tools/bundle-private-game.py \
  --shell-ipa /path/to/MC3-shell-unsigned.ipa \
  --game-root /path/to/game-root \
  --output /path/to/MC3-unsigned.ipa
```

This step never uploads data. It preserves the shell's executable-mode metadata and framework symlinks, computes SHA-256 for all game files, checks streamed bytes against those hashes, and reads back ZIP members for CRC errors. It refuses an existing output, an unexpected app identity, unsafe paths, or an already signed/content-filled shell.

4. Sign the completed private IPA using an authorized signing method/profile. The signer must sign the main executable and embedded MoltenVK library, set any valid profile/entitlements, and preserve bundled content. This kit never adds private/jailbreak entitlements or bypasses signature checks. Signer compatibility is untested. Uploading the completed IPA to any signing service also transmits the bundled game to that service; do that only with the necessary rights and explicit authorization.
5. Install and complete the physical-device acceptance checklist below.

## Content validation and limits

`tools/content.py` accepts only an extracted USA ULUS10021 root, verifies the SFO and executable headers, refuses symlinks/nested archives/PSP firmware updater files, and hashes every included file. The build tool creates a manifest, not a claim about retail authenticity.

Runtime preflight checks sizes for all files and SHA-256 for SFO/UMD/boot files. It does not hash all game assets on the main thread at every launch, which could delay startup or trigger iOS's watchdog. A same-size mutation of a nonboot asset after packaging is not guaranteed to be caught by that preflight. PPSSPP-reported boot failures are shown on the custom recovery screen; arbitrary in-game corruption or emulator crashes cannot be guaranteed to produce that screen.

No game file, encrypted/decrypted PSP executable, key, firmware, or archive is included in this source kit.

## Physical-device acceptance checklist

Record iPhone model, iOS version, signing method, app version, and CPU/GPU backend.

- First install/cold launch: boots MC3 without a game chooser; no loop or unexpected permission prompt.
- Reach actual gameplay and drive a race. Record frame rate, audio quality, input lag, heat, and crashes. An IR-interpreter build may be too slow.
- Touch controls and at least one controller if relevant; pause, resume, reset, and stop/restart the game.
- Save in the game's own menu, terminate normally, cold relaunch, and reload the save. Also test save-state creation/loading separately.
- Background/foreground, phone/audio interruption, rotation, and repeated relaunch.
- Upgrade with the same bundle ID; verify saves still exist. Do not uninstall for this test.
- Asset-free shell: readable missing-content screen, no picker. Intentionally truncated/altered boot-file test build: readable error, no picker.
- External game URL/file and controller dev-menu shortcut do not offer other games.
- Test signed final IPA, not only Xcode's development build. Verify signer's handling of the embedded framework.

## Source and notices

Keep this kit and the complete exact upstream/submodule checkout beside any resulting binary. The build retains matching license/notice files and a source revision manifest in the app. PPSSPP's GPL-2.0-or-later licensing and each dependency's terms remain applicable. Do not assume an upstream link alone covers redistribution obligations for a modified binary. Game redistribution rights are separate; this kit does not provide them.
