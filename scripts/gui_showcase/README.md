# GUI showcase screenshots

This directory contains deterministic documentation-only tooling for the
desktop GUI screenshots. It is excluded from Docker images and must not be
imported by production modules or included in PyInstaller data files.

The generator creates a temporary SQLite channel repository and local channel
logos, injects synthetic service and RTMP states, and puts the Dashboard into
its simulated running visual state so the card glass/activity colors and
progress controls are visible. It waits for the window to be ready and
captures the focused native macOS window. It does not run an update, start a
page service, contact demo stream URLs, invoke FFmpeg, or modify `config/` and
`output/`.

## Generate screenshots

Run on macOS from the repository root:

```bash
pipenv run python scripts/gui_showcase/capture.py
```

The command replaces all four documentation screenshots:

- `docs/images/desktop-ui.png`
- `docs/images/desktop-ui-en.png`
- `docs/images/desktop-ui-dark.png`
- `docs/images/desktop-ui-en-dark.png`

Generate one language with `--language zh_CN` or `--language en`, or one theme
with `--theme light` or `--theme dark`.

## Validate without capturing

```bash
pipenv run python scripts/gui_showcase/capture.py --check
```

This validates channel/result totals, stream references, bilingual fixtures,
and packaging exclusions without opening the GUI.

## Update policy

Regenerate screenshots when changes affect the dashboard, navigation, channel
tables, streaming indicators, visible localization, typography, colors,
spacing, or window layout. Backend-only and non-visual changes do not require
new screenshots.

Before accepting generated images, verify both languages, the running local
service state, active and starting stream indicators, local channel logos,
collapsed navigation, window focus, and native title-bar controls. CI may run
fixture validation, but it must not commit generated images.
