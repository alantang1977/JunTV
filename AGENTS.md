# Repository agent guidelines

## Test execution

- For a focused code change, run only the directly related test module(s)
  before reporting completion.
- Run the full suite (`pipenv run python -m unittest discover -s tests -v`)
  for release work, shared-module changes, or when requested by the user.
- Keep CI's existing full suite for now: it is deterministic and completes in
  seconds locally. Do not split it into separate jobs unless a measured slow
  test category emerges.

## Code comments

- Do not add non-essential comments that explain a fix, patch rationale, or
  transient implementation detail. Add comments only when needed for function
  definitions, key classes or public interfaces, or genuinely complex logic
  that would otherwise be difficult to understand.

## GUI screenshot updates

- Regenerate the checked-in Chinese and English GUI screenshots only when a
  change visibly affects the desktop GUI home page (Dashboard) as it appears
  in the light or dark variants under `docs/images/desktop-ui*.png`.
- Home-page changes include visible Dashboard cards, tables, status or stream
  indicators, collapsed navigation, localization, typography, colors,
  spacing, window layout, or showcase data displayed in those two images.
- Do not regenerate the checked-in screenshots for changes limited to other
  pages, channel drawers or tables, dialogs, menus, settings, tasks, RTMP
  views, interaction behavior, backend code, or other content not visible in
  the home-page screenshots.
- For non-home GUI changes, use targeted temporary screenshots when visual
  verification is useful, but do not update the checked-in home-page images.
- Keep GUI screenshot generators, fixtures, and demo assets under
  `scripts/gui_showcase/`.
- Screenshot tooling must not be imported by production modules under
  `desktop_ui/`, `service/`, `utils/`, or `main.py`.
- Exclude `scripts/gui_showcase/` from Docker build contexts and GUI packaging
  artifacts. Exclude screenshot-specific validation tests from Docker images,
  and do not add fixtures or generated assets to PyInstaller `datas`.
- Demo generation must use temporary directories and must not modify user
  configuration, `config/`, or `output/`.
- When home-page regeneration is required, generate both Chinese and English
  screenshots in both light and dark themes, then verify demo data, service
  status, stream states, local logos, collapsed navigation, window focus, and
  native title-bar controls.
- The checked-in home-page screenshots should show the Dashboard cards in the
  simulated running visual state, including glass/activity colors and progress
  controls. The showcase generator must use Dashboard UI-state injection only;
  it must not call the real update workflow, start update threads, make network
  requests, or invoke FFmpeg.
- After setting the simulated running state, wait for at least one activity
  animation frame before capturing. Validate that the dashboard is running,
  progress is non-zero, and pause/cancel controls are visible.
- Visually inspect regenerated screenshots before staging or committing them.
- CI may report stale screenshots or validate fixtures, but must not
  automatically commit regenerated binary assets.
- When packaging rules change, verify that screenshot tooling is absent from
  both Docker images and GUI distributions.
