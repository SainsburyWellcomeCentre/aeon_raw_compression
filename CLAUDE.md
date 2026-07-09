# aeon_raw_compression — contributor guide

A standalone DataJoint library that losslessly compresses raw ephys acquisition
files (`*_AmplifierData*.bin`, ONIX/Bonsai Neuropixels 2.0) to zarr, verifies a
byte-exact round-trip, and tracks what has been compressed. Deletion of the
originals is supported but hard-disabled in source (see below). It is decoupled
from aeon_mecha: it reads raw files and `Metadata.yml` directly.

- **Design rationale + table-by-table spec:** `raw-ephys-compression-spec.md`
- **User overview + quickstart:** `README.md`
- **Install / deployment / SLURM nightly template:** `templates/README.md`

## Setup

This is a small standalone library, added to your analysis repo (per-user
model) either by `pip install`ing it or as a git submodule next to aeon_mecha.

- Use `uv` for the environment. Most commands need the `dev` extra.
- It connects to the `aeondj` database server, which needs the `cryptography`
  package (a declared dependency). Reuse your project's `datajoint.json` +
  `.secrets/` (host/port/prefix, plus `database.user` / `database.password`
  files). The tables are created under your project's own prefix as
  `<your_prefix>_aeon_raw_compression` — there is no separate shared schema.

## Running it

Import-first (`import aeon_raw_compression as arc`): `arc.activate()`,
`arc.add_trigger("AEONX1/<exp>", placed_by="$USER")`, then either populate the
tables directly (`arc.RawEphysDiscovery.populate()`,
`arc.CompressedFile.populate()`) or the convenience `arc.run()`;
`arc.report_deletable()` lists what's safe to delete. See
`docs/examples/run_compression.py`.

The same actions exist as thin CLI shims (`scripts/*.py`) for cron/SLURM:

1. Scope + discover + compress + verify in one command:
   `uv run python scripts/run.py --experiment "AEONX1/<exp>" --placed-by "$USER"`
2. Or place a trigger then process it:
   `uv run python scripts/add_trigger.py --experiment "AEONX1/<exp>" --placed-by "$USER"`
   then `uv run python scripts/run.py`

All steps are idempotent (already-registered / already-compressed files are
skipped). A nightly SLURM template lives in `templates/`.

## Tests

- Unit (no database, no real data): `uv run --extra dev pytest -m "not integration"`
- Integration (needs `aeondj`; run on the HPC): `uv run --extra dev pytest -m integration`
- Lint: `uv run --extra dev ruff check .`

## Conventions for code changes

- **DataJoint 2.x** (not 0.14.x): use core types (`int32` / `int64` / `float64`
  / `char`), `.to_arrays()` / `.to_dicts()` (not `fetch`), and deferred schema
  activation — importing `pipeline` opens no DB connection; call
  `pipeline.activate(prefix)` before populating.
- **Heavy logic lives in pure, DB-free modules** (`metadata`, `discovery`,
  `compression`) unit-tested against synthetic data in temp dirs; `pipeline.py`
  is thin `make()` wrappers over them, integration-tested against `aeondj`.
- **The codec is fixed and explicit** in `compression.py`
  (`Blosc(cname="zstd", clevel=5, shuffle=BITSHUFFLE)`) — don't rely on
  SpikeInterface's default. No preprocessing (e.g. `correct_lsb()`): it would
  break the byte-exact guarantee.
- **Deletion is hard-disabled** (`DELETION_ENABLED = False` in `pipeline.py`).
  Enabling it is a deliberate, version-controlled code change and requires a
  write/delete-capable raw store — there is intentionally no CLI/config flag.
- **Set `n_jobs` explicitly** (never auto/fractional on SLURM — `os.cpu_count()`
  reports all node cores, not the cgroup allocation). Tuning env vars:
  `AEON_RAW_COMPRESSION_N_JOBS` (default 1); `AEON_RAW_COMPRESSION_CHUNK_DURATION_S`
  (default 30 — zarr time-chunk size, controls the CephFS small-file count);
  `AEON_RAW_COMPRESSION_MIN_AGE_S` (default 3600 — a file is eligible only once
  untouched for this long, so a still-uploading file is never compressed).
