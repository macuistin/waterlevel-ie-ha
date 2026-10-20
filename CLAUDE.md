# Water Level IE

Rules for AI assistants and contributors working in this repo.

## What this is

A Home Assistant custom integration, distributed through HACS, that reads OPW river gauge
data published on waterlevel.ie. It is unofficial: never present it as made or endorsed by
waterlevel.ie or the OPW, and never copy their logos. The display name is "Water Level IE";
the domain is `waterlevel_ie`.

The repo holds the Platinum tier of the Home Assistant quality scale (and so Bronze, Silver
and Gold) and meets the HACS standard. Keep both: the rules under **Standards** say what each
one asks of every change, and `tests/test_standards.py` checks the ones a machine can.
`custom_components/waterlevel_ie/quality_scale.yaml` lists each rule as done, exempt or
todo. Keep it true: update it in the same PR as the change, give every exemption a reason,
and don't claim a rule the code doesn't meet.

## Layout

| Path | Role |
| --- | --- |
| `api.py` | `WaterlevelApi` and pure `parse_*` functions: every request to waterlevel.ie and all parsing |
| `coordinator.py` | Reads the shared latest-readings feed (`LatestReadings`) and each station's CSVs every 15 minutes; `flood_stage()` works out the stage from the held reading |
| `config_flow.py` | Menu, browse by river, or enter a station number |
| `entity.py` | `WaterlevelEntity`: unique id, device info, map coordinates |
| `sensor.py` | `SENSOR_DESCRIPTIONS`, keyed by OPW sensor code or summary key |
| `binary_sensor.py`, `number.py` | Flood Alert and the three threshold numbers |
| `thresholds.py` | Threshold suggestions from a station's Hydro-Data history (pure functions) |
| `diagnostics.py` | Download diagnostics |
| `strings.json`, `icons.json` | Every entity's name and icon, by translation key |
| `brand/` | Icon and logos; sources in `docs/brand/` |

## Rules

- Every behaviour change comes with a test. CI fails below 95% coverage, branches included
  (`fail_under` in `pyproject.toml`); don't lower it to get a PR green.
- Add user-visible changes to `CHANGELOG.md` under `## Unreleased`, grouped as **Added**,
  **Changed**, **Fixes** and **Upgrading**.
- Releasing: rename `## Unreleased` to `## vX.Y.Z` with a sentence or two on what the release
  does, run `python scripts/set_version.py X.Y.Z`, then `--check vX.Y.Z`, in a release PR.
  After it merges, the maintainer pushes the tag `vX.Y.Z` on main and
  `.github/workflows/release.yml` publishes the GitHub release from the changelog section
  (a `b` or `rc` tag publishes a pre-release). Never push a tag or create a release yourself.

## Standards

Each tier includes the ones before it. A change that would break one of these rules either
doesn't ship or updates `quality_scale.yaml`, this file and the tests together, with the
reason. Checks in brackets are tests in `tests/test_standards.py` unless named otherwise.

### Bronze

- Unique ids (`f"{DOMAIN}_{station}_{key}"`) never change once released. English entity
  names don't change either, because new installs derive entity ids from them.
- Every entity subclasses `WaterlevelEntity`, which sets `has_entity_name`
  [`test_entities_use_the_shared_base`].
- Use `entry.runtime_data`, never `hass.data[DOMAIN]`. Only the shared latest-readings feed,
  used by every entry, lives in `hass.data`.
- Don't poll waterlevel.ie more often than every 15 minutes (their API terms).
- The config flow tests the station before it creates an entry, and the entry's unique id is
  the station, so a station can't be added twice. Setup raises `ConfigEntryNotReady` when the
  station can't be read at all.
- Every config flow step, error and abort has a test in `tests/test_config_flow.py`
  [`test_every_config_flow_step_is_tested` checks each step], and every flow field has a
  `data_description` [`test_every_flow_field_has_a_description`].
- `manifest.json` `requirements` stays empty; a dependency must be a pinned public PyPI
  package built in CI [`test_requirements_are_pinned_pypi_packages`].
- There are no actions. Adding one means registering it in `async_setup`, documenting it in
  the README, raising `ServiceValidationError` or `HomeAssistantError` for bad input, and
  moving action-setup, docs-actions and action-exceptions from exempt to done.
- `brand/` keeps its icon and logos, original artwork, never waterlevel.ie or OPW logos
  [`test_brand_assets_exist`].
- Each done docs-* rule in `quality_scale.yaml` names the README heading that meets it, and
  that heading exists [`test_docs_rules_name_a_readme_heading`]. Change the README section in
  the same PR as the behaviour it describes.

### Silver

- Every platform sets `PARALLEL_UPDATES` [`test_platforms_set_parallel_updates` in
  `tests/test_metadata.py`].
- The coordinator keeps each sensor's last good reading through an outage and raises
  `UpdateFailed` (entities unavailable) only once the newest reading is older than
  `stale_after`. The freshness entities (Last Reading, Data Age, Data Stale) stay
  available. Per-sensor failures log at debug, so an outage logs once and the recovery once.
  Setup retries only when the station can't be read at all; a station with only old readings
  sets up stale.
- Unloading an entry unloads every platform, and anything an entry starts is stopped on unload.
- Every setup field and option is a row in the README's Configuration tables, by its English
  label [`test_readme_documents_every_setup_field_and_option`].
- `manifest.json` `codeowners` names the owner [`test_integration_has_an_owner`].
- waterlevel.ie needs no account. Anything that adds credentials needs a reauth flow.

### Gold

- A new entity needs a translation key, a name in `strings.json` and an icon in `icons.json`
  [`test_every_entity_has_a_name_and_icon` in `tests/test_metadata.py`]. It also gets a device
  class where one fits, an entity category if it is diagnostic or config, and is disabled by
  default if most people won't want it.
- Every exception shown to users is raised with `translation_domain=DOMAIN` and a
  `translation_key` that has a message under `exceptions` in `strings.json`
  [`test_raised_exceptions_are_translated`].
- `translations/en.json` is a byte copy of `strings.json`, and every other translation
  (`ga.json`) has the same keys and placeholders [`tests/test_metadata.py`]. A new string
  goes into every translation in the same PR; mark a machine-drafted one for review by a
  fluent speaker.
- One device per station. Diagnostics hold only public station data; anything else added
  to them must be redacted.
- The exemptions hold while each entry is one station with nothing on the network to
  discover and nothing the user can repair. Revisit dynamic-devices, stale-devices,
  discovery and repair-issues if that changes.

### Platinum

- Every change passes `mypy` (strict, configured in `pyproject.toml`), which runs in the
  `typing` CI job [`test_mypy_is_strict_and_runs_in_ci`]. Use real types rather than `Any`
  where one exists, and every `# type: ignore` names its error code and says why
  [`test_type_ignores_name_their_error`].
- Use `async_get_clientsession(hass)`; never open an `aiohttp.ClientSession`. Requests go
  through `WaterlevelApi`, which raises `WaterlevelError` (or `StationNotFound` for a 404).
- async-dependency is exempt only while there is no dependency
  [`test_no_dependency_while_async_dependency_is_exempt`]. A dependency must be an asyncio
  library on PyPI that takes Home Assistant's session.

### HACS

- The HACS action, hassfest and the tests run on every pull request and must pass; never
  skip or weaken them [`test_validation_runs_on_every_pull_request`].
- `manifest.json` keeps `domain`, `name`, `documentation`, `issue_tracker`, `codeowners`
  and `version`, and `hacs.json` keeps the same `name` and the minimum Home Assistant
  [`test_hacs_manifest_keys`].
- `waterlevel_ie` stays the only integration under `custom_components/`
  [`test_one_integration_in_the_repo`].
- The minimum Home Assistant version is in `hacs.json`, and `.github/workflows/tests.yml`
  tests it. Change both together [`test_ci_tests_the_minimum_home_assistant`].
- Keep the version in `manifest.json` and `pyproject.toml` the same
  [`test_manifest_and_project_versions_match`]. Every release is a published GitHub release
  from a `vX.Y.Z` tag, as above.
- The repository stays public with issues enabled, and keeps its description and its `hacs`
  and `home-assistant` topics.

## Commands

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
mypy
pytest --cov
```

The latest Home Assistant needs Python 3.14. CI also runs the floor in `hacs.json` on
Python 3.13, plus hassfest and the HACS action.
