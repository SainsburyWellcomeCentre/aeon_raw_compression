# Contributing to aeon_raw_compression

Thanks for helping improve the raw-ephys compression pipeline. This is a small
standalone DataJoint library (DataJoint 2.x). See `CLAUDE.md` for the design
conventions and `raw-ephys-compression-spec.md` for the rationale.

## Setup

The project uses [uv](https://docs.astral.sh/uv/). Most commands need the `dev`
extra.

```bash
uv sync --extra dev        # create the environment
```

To connect to the `aeondj` database server you also need a `datajoint.json` plus
a `.secrets/` directory (`database.user` / `database.password`) — reuse your
project's existing config. These are gitignored and never committed.

## Tests

```bash
uv run --extra dev pytest -m "not integration"   # unit tests: no DB, no real data
uv run --extra dev pytest -m integration          # integration: needs aeondj (run on the HPC)
```

Set `AEON_TEST_PREFIX=<your_prefix>_` for the integration suite; it creates a
throwaway schema and drops it at the end. The real-data test additionally needs
`AEON_REAL_CHUNK` pointing at a real `*_AmplifierData_*.bin` (compute node).

## Style

```bash
uv run --extra dev ruff check .        # lint
uv run --extra dev ruff format .       # format
uv run --extra dev pre-commit run --all-files   # everything (ruff, codespell, whitespace)
```

Install the git hook once with `uv run --extra dev pre-commit install` so checks
run on every commit.

## Pull requests

Describe what changed and why. No "Test plan" section is needed. Keep changes
focused; heavy logic lives in the pure `metadata`/`discovery`/`compression`
modules (unit-tested), with `pipeline.py` a thin `make()` layer over them.
