# Deployment: per-user submodule + nightly compression

`aeon_raw_compression` is a standalone DataJoint library. For v1 each user runs
it on their own data and compute quota by adding it as a **submodule** next to
their analysis repo (alongside `aeon_mecha`). A centralized cron deployment on
the DataJoint database server is possible later from the same library, but is
not set up here.

The library connects to the **`aeondj`** database server (the older `aeon-db`
is legacy). Connecting to `aeondj` needs the `cryptography` package, which is a
declared dependency.

## 1. Add as a submodule

```bash
cd <your-analysis-repo>
git submodule add https://github.com/SainsburyWellcomeCentre/aeon_raw_compression
git commit -m "Add aeon_raw_compression submodule"
```

## 2. DataJoint config

The tables live under **your project's own DataJoint prefix** -- there is no
separate compression schema. Reuse the same `datajoint.json` + `.secrets/` you
use for the rest of your project (copy them next to the library, or point at
them). The schema name becomes `<your_prefix>_aeon_raw_compression`.

`datajoint.json` holds host/port/prefix/stores; the adjacent `.secrets/`
directory holds `database.user` and `database.password` files containing just
the credential values. (DJ 2.x format -- not environment variables, not the
deprecated `dj_local_conf.json`.)

To host the tables under a different prefix, pass `--prefix` to the scripts (or
`pipeline.activate(prefix=...)`).

## 3. Place a discovery trigger

Discovery is **scoped** -- you say which directory to scan. Nothing is scanned
until a trigger is placed.

```bash
# all epochs under an experiment:
uv run python scripts/add_trigger.py \
    --experiment "AEONX1/abcGolden01" --placed-by "$USER"

# or restrict to a single epoch:
uv run python scripts/add_trigger.py \
    --experiment "AEONX1/abcGolden01" --epoch "2026-05-11T07-50-11" \
    --placed-by "$USER"
```

A relative `--experiment` is resolved against the Ceph raw-data root
(`/ceph/aeon/aeon/data/raw` by default); an absolute path is used as-is.

## 4. Run discovery + compression

```bash
uv run python scripts/run.py --placed-by "$USER"
```

This populates `RawEphysDiscovery` (registers complete files) then
`CompressedFile` (compress to zarr **and** verify the byte-exact round-trip, in
one atomic step). Re-running is safe and idempotent -- already-registered and
already-compressed files are skipped.

## 5. Schedule the nightly job

```bash
mkdir -p logs
sbatch templates/nightly_compress.sbatch
```

The template is CPU-only (no GPU). To run every night, either add a `crontab`
entry on a submit host that calls `sbatch templates/nightly_compress.sbatch`,
or have the job re-submit itself at the end with
`sbatch --begin=now+1day templates/nightly_compress.sbatch`. See the comments
in the template for the `n_jobs` / `os.cpu_count()` SLURM trap.

### Tuning the final-chunk guard

Discovery registers a recording's final (highest-numbered) chunk only once the
epoch is finished and the file is quiescent. Two env vars tune that guard (the
spec's `tune-on-ceph` open question); both default conservatively:

- `AEON_RAW_COMPRESSION_QUIESCENCE_S` — how long a file's size/mtime must be
  stable before it counts as quiescent. Default `1800` (30 min).
- `AEON_RAW_COMPRESSION_EPOCH_MAX_AGE_S` — for the rig's *last* epoch (which
  never gets a newer sibling epoch directory), how long the epoch must be
  completely stable before it counts as finished. Default `21600` (6 h).
  Without this fallback the last epoch's final chunk would never register.

## Storage permissions (read this before enabling deletion)

Two operations need **write/delete** access to the raw ephys store:

- **Compression** writes the `.zarr` directory *alongside* the original `.bin`.
- **Deletion** (disabled in v1) would remove the original `.bin`.

That store is **read-only for some accounts by design** (currently the case for
the maintainer's account). Before either can run against real data you must
establish a permission structure -- e.g. test on a writable copy of a data file
first, then arrange write access on the real store. Sort this out before
relying on the nightly job.

## Deletion is disabled in v1

`OriginalDeletion` is hard-disabled in source (`DELETION_ENABLED = False` in
`pipeline.py`). There is no CLI flag or config to enable it -- turning it on is
a deliberate, version-controlled code change, made only once the team decides
the time has come and a write/delete-capable store is in place.

## Running the integration tests (HPC)

The DataJoint-layer tests are marked `integration` and run on the HPC against
`aeondj` (configured as above) -- they are skipped in the default local run:

```bash
module load uv
uv run pytest -m integration
```

Local development/unit tests need no database and no real data:

```bash
uv run pytest -m "not integration"
```
