# aeon_raw_compression

A small, standalone [DataJoint](https://datajoint.com) library that losslessly
compresses raw ephys acquisition files (the large `*_AmplifierData_*.bin`
Neuropixels 2.0 binaries) to [zarr](https://zarr.dev), **verifies a byte-exact
round-trip**, and tracks what has been compressed — with an optional, deliberately
hard-to-enable step to delete the originals once a recording is archival.

It is decoupled from `aeon_mecha`: it reads raw files and `Metadata.yml` directly
from the filesystem, so it works even for users who do not process their data
through DataJoint. For v1 each user runs it on their own data and compute quota
by adding it as a submodule next to their analysis repo; a centralized cron
deployment is possible later from the same library.

Compression uses Blosc-zstd (`cname="zstd"`, `clevel=5`,
`shuffle=BITSHUFFLE`, recorded as `blosc-zstd-5-bitshuffle`), which measured a
~1.95× ratio (≈49% savings) with a verified byte-for-byte round-trip on Aeon NP2
data. The codec is constructed explicitly in code, so it stays fixed even if
SpikeInterface's default changes. No preprocessing (e.g. `correct_lsb()`) is
applied — that would break the byte-exact guarantee.

## The pipeline

Four DataJoint tables, each a thin `make()` over the pure modules:

```
RawEphysDiscoveryTrigger (Manual)   you scope which directory to scan + who placed it
        │
RawEphysDiscovery (Imported)        scans for COMPLETE raw files
  └─ RawEphysFile (Part)            one row per file eligible for compression
        │
CompressedFile (Computed)           compress to zarr AND verify round-trip (atomic)
        │
OriginalDeletion (Computed)         delete the original — HARD-DISABLED in v1
```

A file is registered only when it is provably **complete** (its successor chunk
exists, or — for the final chunk — the epoch is finished and the file is
quiescent). A `CompressedFile` row exists only if compression *and* verification
both passed.

## Install (per-user submodule)

```bash
cd <your-analysis-repo>
git submodule add https://github.com/SainsburyWellcomeCentre/aeon_raw_compression
git commit -m "Add aeon_raw_compression submodule"
```

The library connects to the `aeondj` database server (the older `aeon-db` is
legacy; connecting needs the `cryptography` package, a declared dependency). It
reuses your project's existing `datajoint.json` + `.secrets/` and creates its
tables under **your project's own prefix** as `<your_prefix>_aeon_raw_compression`
(per-project tracking; there is no separate shared schema in v1).

## Quickstart

```bash
# 1. Scope a directory to scan (nothing is scanned until a trigger is placed):
uv run python scripts/add_trigger.py --experiment "AEONX1/<exp>" --placed-by "$USER"

# 2. Discover complete files, compress them to zarr, and verify the round-trip:
uv run python scripts/run.py --placed-by "$USER"
```

Re-running is safe and idempotent — already-registered and already-compressed
files are skipped. To override the host prefix, pass `--prefix` (or
`pipeline.activate(prefix=...)`).

For nightly automation on the SWC HPC and the full deployment/permissions guide,
see [`templates/README.md`](templates/README.md).

## Deletion is disabled in v1

`OriginalDeletion` is hard-disabled in source (`DELETION_ENABLED = False` in
`pipeline.py`). There is no CLI flag or config to enable it — turning it on is a
deliberate, version-controlled code change, made only once the team decides and a
write/delete-capable raw store is in place. The discover/compress automation
never touches that table.

## Tests

```bash
# Unit tests — no database, no real data:
uv run --extra dev pytest -m "not integration"

# Integration tests — run on the HPC against aeondj (datajoint.json + .secrets):
uv run --extra dev pytest -m integration
```

## More

- Design rationale and table-by-table spec: [`raw-ephys-compression-spec.md`](raw-ephys-compression-spec.md)
- Deployment, SLURM nightly template, storage permissions: [`templates/README.md`](templates/README.md)
