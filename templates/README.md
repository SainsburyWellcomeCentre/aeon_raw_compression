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

### Tuning knobs (env vars)

- `AEON_RAW_COMPRESSION_MIN_AGE_S` — a file is eligible only once it has been
  untouched (mtime) for this long, so a still-uploading file is never compressed.
  Default `3600` (1 h).
- `AEON_RAW_COMPRESSION_CHUNK_DURATION_S` — zarr time-chunk size in seconds.
  Larger => fewer on-disk chunk files (kinder to CephFS) but larger minimum
  reads. Default `10`.
- `AEON_RAW_COMPRESSION_N_JOBS` — explicit SpikeInterface `n_jobs`. Default `1`
  (safe on SLURM; never use a fractional value there).

### Where the compressed data lands

The `.zarr` is written under the **processed** data root
(`PROCESSED_DATA_ROOT`, default `/ceph/aeon/aeon/data/processed`), mirroring the
raw sub-path — never beside the read-only raw `.bin`. Override the roots via
`pipeline.activate(raw_data_root=..., processed_data_root=...)`.

## Deleting originals (manual green-light in v1)

The shipped deletion workflow is a **read-only report**:

```bash
uv run python scripts/report_deletable.py --placed-by "${USER}"
```

It lists verified-compressed raw files that still exist and haven't been
actioned (paths + reclaimable GB) and writes nothing. Delete those paths
manually on the **recording computer** — the only machine with delete rights on
the Ceph raw store (it put the files there via RoboCopy).

Automated deletion (`RawEphysFileDeletion.make`) is hard-disabled in source
(`DELETION_ENABLED = False` in `pipeline.py`); enabling it is a deliberate,
version-controlled code change, made only once the team decides.

## Deferred to the team (solve once everything else is verified)

Two things still need real infrastructure access and a team decision:

1. **Real deletion mechanism / permissions.** v1 only produces the manual
   green-light list; the recording computer deletes on Ceph. If/when we want the
   pipeline to delete automatically, that needs delete access from wherever it
   runs (and enabling `DELETION_ENABLED`). Compression itself no longer needs
   raw-store write access — it writes to the processed root.

2. **Tuning `AEON_RAW_COMPRESSION_MIN_AGE_S`.** The 1 h default guards against
   compressing a file mid-RoboCopy; confirm it's comfortably longer than the
   real upload settle time by watching acquisition on Ceph. (Env var, so tuning
   needs no code change.)

Raise both with the team once the pipeline is reviewed and working.

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
