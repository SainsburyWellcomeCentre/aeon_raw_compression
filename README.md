# aeon_raw_compression

A small, standalone [DataJoint](https://datajoint.com) library that losslessly
compresses raw ephys acquisition files (the large `*_AmplifierData_*.bin`
Neuropixels 2.0 binaries) to [zarr](https://zarr.dev), **verifies a byte-exact
round-trip**, and tracks what has been compressed. Deleting the originals stays
**manual**: the library reports which files are safe to remove, and you delete
them from the recording computer when you're ready (see
[Deleting originals](#deleting-originals-manual-green-light)).

It is decoupled from `aeon_mecha`: it reads raw files and `Metadata.yml` directly
from the filesystem, so it works even for users who do not process their data
through DataJoint. The compressed zarr is written under a **processed** data root
(mirroring the raw sub-path), never beside the read-only raw `.bin`. For v1 each
user runs it on their own data and compute quota (pip-installed or as a
submodule); a centralized cron deployment is possible later from the same library.

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
RawEphysFileDeletion (Computed)     delete the original — automated path DISABLED in v1
```

A file is registered only when it is provably **complete**: it has been left
untouched for at least `AEON_RAW_COMPRESSION_MIN_AGE_S` (default 1 h), so it is
not still being written/uploaded to Ceph. A `CompressedFile` row exists only if
compression *and* verification both passed.

Each `CompressedFile` row also stores a `content_hash` (SHA-256 of the original
`.bin`). Verification already proves the round-trip at write time; the hash is a
**durable digest** for the deletion era — once an original is deleted, a future
tool can re-confirm the zarr still decodes to those exact bytes (by hashing
`si.load(zarr).get_traces().tobytes()`) without needing the original present.

## Install

The library is a standard pip-installable package. Delivery method does not
change how it works — you `import aeon_raw_compression` and call
`pipeline.activate(<prefix>)` the same way regardless (importing opens no
database connection).

```bash
# Recommended: pip install a released tag (or @main for the latest)
pip install "git+https://github.com/SainsburyWellcomeCentre/aeon_raw_compression@v0.1.0"
```

```bash
# Alternative: add as a git submodule next to your analysis repo
cd <your-analysis-repo>
git submodule add https://github.com/SainsburyWellcomeCentre/aeon_raw_compression
git commit -m "Add aeon_raw_compression submodule"
```

## Setup (DataJoint config)

The library connects to the `aeondj` database server (the older `aeon-db` is
legacy; connecting needs the `cryptography` package, a declared dependency) and
creates its tables under **your project's own prefix** as
`<your_prefix>_aeon_raw_compression` (per-project tracking; no separate shared
schema in v1).

Reuse the DataJoint 2.x config your project already uses for `aeondj` — a
`datajoint.json` plus an adjacent `.secrets/` directory (both gitignored; never
commit them):

```
datajoint.json
.secrets/
    database.user        # the username, on its own
    database.password    # the password, on its own
```

`datajoint.json` holds host/port/prefix (no `stores` block is needed — the
tables store plain paths, not external-store blobs):

```json
{"database": {"host": "aeondj", "port": 3306, "database_prefix": "your_prefix_"}}
```

## Usage

Everything is available from the top-level `import aeon_raw_compression as arc`.
Importing opens no database connection; `activate()` binds the tables to your
project's prefix. Nothing is scanned until you place a trigger.

```python
import aeon_raw_compression as arc

arc.activate()                                   # your project's DataJoint prefix
arc.add_trigger("AEONX1/<exp>", placed_by="me")  # scope what to scan + who scoped it

# Option A — populate the exposed tables directly (inspect / restrict / re-run each):
arc.RawEphysDiscovery.populate()                 # register complete raw files
arc.CompressedFile.populate()                    # compress to zarr AND verify round-trip

# Option B — or the one-call convenience, equivalent to the two populates above:
#     summary = arc.run()                        # -> "registered=26 compressed=26 errored=0"

print(arc.report_deletable())                    # raw files now safe to delete (manually)
```

The four tables (`RawEphysDiscoveryTrigger`, `RawEphysDiscovery`,
`CompressedFile`, `RawEphysFileDeletion`) are exposed on `arc`, so you can query
and populate them like any DataJoint table. Re-running is safe and idempotent —
already-registered and already-compressed files are skipped. Pass a prefix to
`arc.activate("your_prefix_")` to override the project default.

A copy-and-edit version of this walkthrough is at
[`docs/examples/run_compression.py`](docs/examples/run_compression.py).

### Automation (cron / SLURM)

The same actions are available as thin CLI wrappers (`scripts/*.py`) for a
nightly job — no install needed, so they work from a submodule checkout too. A
single `run.py` call both scopes and processes the day's recordings:

```bash
# Scope + discover + compress + verify, in one command (idempotent):
uv run python scripts/run.py --experiment "AEONX1/<exp>" --placed-by "$USER"

# Or place a trigger and process it in two steps:
uv run python scripts/add_trigger.py --experiment "AEONX1/<exp>" --placed-by "$USER"
uv run python scripts/run.py
```

For the SWC HPC nightly template and the full deployment/permissions guide, see
[`templates/README.md`](templates/README.md).

## Deleting originals (manual green-light)

Deletion is manual by design, and stays that way for now. The shipped workflow
is a **read-only report** (`arc.report_deletable()`, or the CLI below), not
automated deletion:

```bash
uv run python scripts/report_deletable.py
```

It lists every verified-compressed raw file that still exists and hasn't been
actioned (paths + total reclaimable GB) and **writes nothing**. You then delete
those paths manually on the recording computer — the only machine with delete
rights on the Ceph raw store (it wrote them there via RoboCopy).

Automated deletion (`RawEphysFileDeletion.make`) is hard-disabled in source
(`DELETION_ENABLED = False` in `pipeline.py`); enabling it is a deliberate,
version-controlled code change made only once the team decides. The
discover/compress automation never touches that table.

## Tests

```bash
# Unit tests — no database, no real data:
uv run --extra dev pytest -m "not integration"

# Integration tests — run on the HPC against aeondj (datajoint.json + .secrets).
# Set AEON_TEST_PREFIX to a prefix you can create schemas under; the suite makes
# a throwaway schema and drops it at the end:
AEON_TEST_PREFIX=<your_prefix>_ uv run --extra dev pytest -m integration
```

The integration suite proves the DataJoint pipeline end-to-end on synthetic
data. One further test (`test_real_chunk_full_pipeline_roundtrip_and_deletion`)
runs the **whole pipeline on a copy of one real chunk** — real `Metadata.yml`
parse, byte-exact compress/verify, the `content_hash` recipe, and (on the safe
copy) real deletion. It runs only when `AEON_REAL_CHUNK` points at a real
`*_AmplifierData_*.bin` from an enabled probe, and needs a compute node with a
roomy temp dir (the copy + zarr take ~3× the chunk size):

```bash
AEON_TEST_PREFIX=<your_prefix>_ \
AEON_REAL_CHUNK=/path/to/<epoch>/<device>/<name>_AmplifierData_0.bin \
uv run --extra dev pytest -m integration --basetemp=/path/to/roomy/scratch
```

## More

- Design rationale and table-by-table spec: [`raw-ephys-compression-spec.md`](raw-ephys-compression-spec.md)
- Deployment, SLURM nightly template, storage permissions: [`templates/README.md`](templates/README.md)
