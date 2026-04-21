# Video Player Release Build

This folder contains the local release pipeline for the macOS app.

## Build

Run:

```bash
./video-player-build/package_app.sh
```

That will:

- regenerate the `.icns` app icon
- build a standalone `.app` with PyInstaller
- ad-hoc sign the app for local use
- create both `.zip` and `.dmg` artifacts in `release/`
- replace the installed copy in `/Applications`

If you only want artifacts and do not want to replace the installed app:

```bash
./video-player-build/package_app.sh --skip-install
```

## Smoke Test

After building, verify the packaged app and HTTP API:

```bash
./video-player-build/smoke_test.sh "/Applications/Video Player.app" /tmp/video_player_resume_test.mp4
```

## Signing

By default the script uses ad-hoc signing (`-`).

To use a real Developer ID identity instead:

```bash
export VIDEO_PLAYER_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./video-player-build/package_app.sh
```

## Metadata

App version, bundle identifier, minimum macOS version, and settings identifiers
all live in:

- `video_player_metadata.py`
