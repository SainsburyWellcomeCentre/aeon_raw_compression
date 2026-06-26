# Standalone Raw Ephys Data Compression Pipeline

Spec for a standalone DataJoint library that compresses raw ephys
acquisition files, verifies lossless round-trip integrity, and optionally
deletes the originals. Decoupled from aeon_mecha: it runs as a per-user
submodule (the v1 model) with a path to a centralized service later.

**Status:** Discussed at the 2026-06-11 meeting (three approaches: a DJ table
in the existing pipeline, a fully standalone non-DJ process, or a standalone
DJ pipeline -- the standalone DJ approach was chosen). Refined at the
2026-06-18 meeting and in subsequent review into the design below: a
standalone library, discovery driven by a scoped trigger table, run per-user
first with a path to centralized automation. **Reviewed and approved; ready
for implementation.**

---

## Problem

Raw ephys acquisition files are the single largest consumer of disk space on
Ceph. Each Neuropixels 2.0 probe generates approximately 80 GB per hour of
recording (384 channels, 30 kHz, uint16, written in 10-minute chunks of
~13.4 GB each). A multi-day experiment with multiple shanks can produce tens
of terabytes of raw data.

This raw data is never modified after acquisition. The spike sorting
pipeline reads it during PreProcessing but does not write back to it. Once
an experiment is complete (and verified), the raw files are archival.

Compression testing on `es/compression-spec` measured a 1.95x compression
ratio (49% space savings) using Blosc-zstd on Aeon's NP2 data, with
verified byte-for-byte lossless round-trip on all 12 chunks tested.
Compression takes about 15 minutes per hour of recording per shank.
Decompression takes about 4 minutes per hour.

## Why a standalone library, run per-user first

The aeon_mecha spike sorting pipeline is a template: each user creates their
own analysis repo, submodules aeon_mecha, and runs their own pipeline
instance. Compression is delivered as a **separate standalone library**
rather than a table inside aeon_mecha, so that:

- It is decoupled from the analysis pipeline and can compress data even for
  users who do not process through DataJoint at all.
- It tracks what has been compressed and verified in DataJoint tables
  (per-project for v1; see Database) without folding that state into
  aeon_mecha's analysis tables.
- The same library can later be deployed centrally without a rewrite.

A standalone DJ library also gives us job tracking, error handling, and
logging for free.

**Execution starts per-user, not centralized.** For the initial rollout,
users add this library as a submodule next to aeon_mecha and run it on their
own raw data. Two reasons drive per-user-first:

- **Compute quotas are per-user.** A single central process compressing
  everyone's data would consume one person's quota. Per-user runs spend each
  user's own quota.
- **Data control.** Each user decides when they trust the compression enough
  to (eventually) enable deletion of their originals.

Running per-user first also ensures the work actually gets done and lets
experimenters build confidence before any hand-off to automation. A
centralized deployment (a cron on the DataJoint database server) stays open
as a later option -- the same library supports both. For v1, compression
state lives in each project's own schema (see Database); the discovery trigger
still records who placed it, for audit and to ease a possible future move to a
shared schema.

## Architecture

### Repo structure

```
aeon_raw_compression/
|-- pyproject.toml
|-- datajoint.json
|-- .secrets/
|   |-- database.user
|   `-- database.password
|-- src/
|   `-- aeon_raw_compression/
|       |-- __init__.py
|       |-- pipeline.py       # DJ schema + tables (trigger, discovery, compress, delete)
|       |-- discovery.py      # Ceph filesystem scanner (driven by a trigger)
|       `-- compression.py    # SI compress/decompress/verify logic
|-- scripts/
|   |-- add_trigger.py        # CLI: insert a RawEphysDiscoveryTrigger (dirs + who placed it)
|   `-- run.py                # CLI: populate discovery -> compress+verify; cron/SLURM entry point
|                             # (no deletion CLI in v1 -- deletion is source-gated; see OriginalDeletion)
|-- templates/
|   |-- nightly_compress.sbatch   # SLURM template: compress the day's recordings overnight
|   `-- README.md                 # setup: per-user submodule + cron/SLURM instructions
`-- tests/
    `-- ...
```

This library is meant to be added as a git submodule inside a user's analysis
repo (alongside aeon_mecha). The `templates/` directory carries the
ready-to-adapt SLURM/cron automation and setup instructions.

### Dependencies

- `spikeinterface` -- reading binary, saving zarr, loading zarr
- `datajoint` -- pipeline tracking
- `cryptography` -- required to connect to the `aeondj` database server
- `numpy` -- byte-for-byte comparison

`Metadata.yml` is parsed with the standard-library `json` module -- despite
the `.yml` extension the file is JSON (see Metadata.yml parsing) -- so no YAML
dependency is needed.

**Pin versions.** The zarr `save()` path and the Blosc default compressor have
shifted across SpikeInterface releases; an unpinned upgrade could silently
write a *different* codec than the one validated, undermining the losslessness
guarantee. Pin `spikeinterface` to the tested release and `datajoint==2.2.2`
(the version Aeon runs). As a belt-and-braces measure, the compression code
constructs the Blosc compressor explicitly (`cname="zstd"`, `clevel=5`,
`shuffle=BITSHUFFLE`) rather than relying on whatever the installed SI version
defaults to.

No dependency on aeon_mecha. The library reads raw files and Metadata.yml
directly from the filesystem.

### Database

New Aeon work uses the `aeondj` database server (the older `aeon-db` is now
legacy). Connecting to `aeondj` requires the `cryptography` package (see
Dependencies).

The compression tables are **not** a separate schema with their own dedicated
prefix. In the per-user model, each analysis project sets its own DataJoint
credentials and database prefix at the project level (alongside the aeon_mecha
submodule), and there is no clean way to hand compression a separate prefix of
its own. So the library's tables are created **under the project's own
prefix**, living as a few standalone tables next to the rest of that project's
schemas. Tracking is therefore **per-project** for v1: each project records
what it has compressed and verified, for its own data.

A later move to a single shared/central schema (for cross-experimenter
tracking) remains possible, but it would require porting each project's
compression records into the common schema. That is explicitly deferred, not
designed in now.

---

## Table design

### RawEphysDiscoveryTrigger (Manual)

A trigger scopes a discovery run to specific directories and records who
requested it. Discovery is deliberately *not* a blanket "scan everything"
operation -- whoever places a trigger decides which directories are in scope.
The `placed_by` field records who requested the run, for audit and to ease a
possible future migration into a shared cross-experimenter schema (in v1 each
project's schema already holds only its own data).

```
trigger_time     : datetime      # when the trigger was placed (part of PK)
placed_by        : varchar(64)   # who placed it -- audit / future migration
---
experiment_path  : varchar(255)  # directory in scope, e.g. "AEONX1/abcGolden01"
epoch_path=''    : varchar(255)  # optional: restrict to one epoch; '' = all epochs under experiment_path
```

Two notes on the key: (1) The primary key is `(trigger_time, placed_by)`.
Keeping `placed_by` in the key lets two different people place triggers in the
same second without colliding. The only remaining collision -- the same person
placing two triggers in the same second -- simply raises a duplicate-key error
(no double insert, no corruption), which is acceptable since triggers are not
placed in tight scripted loops. (2) `epoch_path` here (an optional *scoping*
directory) is distinct from the `epoch_dir` recorded later on each
`RawEphysFile` (the epoch a found file actually lives in); the names differ
because the concepts do.

### RawEphysDiscovery (Imported)

Runs the discovery scan for one trigger and registers the raw files it finds.
As an Imported table, discovery itself gets DJ job tracking and error
handling.

```
-> RawEphysDiscoveryTrigger
---
num_files_found  : int32         # how many new raw files this run registered
discovery_time   : datetime
```

**make() logic:** scan the directories named by the trigger (see File
discovery) and insert one `RawEphysFile` part row per *complete* raw binary
found. make() skips any file already present in the `RawEphysFile` registry
from a prior trigger; a `unique index (file_path)` on the part table enforces
this at the database level (see RawEphysFile), so the same physical file is
never registered or compressed twice even if two triggers overlap or run
concurrently.

#### RawEphysFile (Part of RawEphysDiscovery)

Registry of raw files eligible for compression. One row per physical binary
file, globally.

```
-> RawEphysDiscovery
file_path         : varchar(512)  # full path on Ceph (part of PK)
---
experiment_path   : varchar(255)  # e.g. "AEONX1/abcGolden01"
epoch_dir         : varchar(64)   # e.g. "2026-05-11T07-50-11"
device_name       : varchar(64)   # e.g. "NeuropixelsV2"
probe_label       : varchar(32)   # e.g. "ProbeB"
file_name         : varchar(128)  # e.g. "NeuropixelsV2_ProbeB_AmplifierData_0.bin"
file_size_bytes   : bigint        # original file size
num_channels      : int32         # from Metadata.yml
sampling_frequency: float         # Hz (typically 30000)
unique index (file_path)          # one row per physical file across ALL triggers
```

The `unique index (file_path)` makes the database guarantee that each
physical file is registered exactly once within the project's schema (the
scope that matters for the per-project v1 model), even if two triggers cover
overlapping directories or run concurrently. Discovery's make() still skips
files already in the registry on the normal path (so re-scans are quiet); the
index is the backstop that makes "compress once" a hard guarantee rather than
a matter of make() getting the check right.

### CompressedFile (Computed)

Compresses each raw file to zarr **and verifies the round-trip in the same
step**. There is deliberately no separate verification table: you never want
a compressed file that hasn't been verified, and verification (~4 min) is
cheap relative to compression (~15 min). One row per file that compressed
*and* passed verification.

```
-> RawEphysDiscovery.RawEphysFile
---
zarr_path              : varchar(512)  # path to zarr directory on Ceph
compressed_size_bytes  : bigint
compression_ratio      : float         # original / compressed
compression_time_s     : float         # wall-clock seconds, compression only
decompression_time_s   : float         # wall-clock seconds, round-trip verification
codec_name             : varchar(64)   # e.g. "blosc-zstd-5-bitshuffle"
checksum_match         : bool          # True for every inserted row (see make logic)
num_samples            : bigint        # sample count; original and zarr verified equal
execution_time         : datetime
```

**make() logic:**

1. Load the binary file via `spikeinterface.extractors.read_binary()` using
   parameters from the `RawEphysFile` part row (num_channels,
   sampling_frequency, dtype uint16).
2. **Compress.** First remove any stale/partial zarr already at `zarr_path`
   (a prior run killed mid-write -- SLURM timeout, OOM -- leaves a partial
   directory but no row; `recording.save()` errors if the folder exists, which
   would wedge the file permanently in `jobs.errors`). Then save as zarr:
   `recording.save(format="zarr", folder=zarr_path)`. The zarr directory is
   placed alongside the original binary and **keeps the original's stem** --
   only the extension differs (`..._AmplifierData_0.bin` ->
   `..._AmplifierData_0.zarr/`). Same stem, different extension is the contract
   the aeon_mecha read-side resolver relies on (see Companion aeon_mecha PR).
   Measure compressed size (sum of all files in the zarr directory) and timing.
3. **Verify (round-trip).** Reload the zarr via `si.load()` and the original
   via `read_binary()`. Compare sample counts, then compare data arrays
   chunk-by-chunk (memory-mapped, to avoid loading the whole file at once):
   each chunk of the original is read, the corresponding samples are pulled
   from the zarr recording, and the arrays are compared element-by-element.
4. **On mismatch, raise.** If sample counts differ or any sample differs,
   `make()` raises an informative error. Because it raised, **no row is
   inserted** and the failure is recorded in `jobs.errors`; the untrusted
   zarr directory is removed so a retry starts clean. A file that fails
   verification is therefore never tracked as compressed and can never be
   deleted.
5. **On success, insert one row** with the compression metrics and
   verification results (`checksum_match=True`, `decompression_time_s`,
   `num_samples`).

The codec is the exact configuration validated on `es/compression-spec`:
Blosc with `cname="zstd"`, `clevel=5`, `shuffle=Blosc.BITSHUFFLE` (which is
also SpikeInterface's default zarr compressor), recorded as
`codec_name="blosc-zstd-5-bitshuffle"`. This is the configuration that
produced the 1.95x mean ratio (1.95x-1.98x range, 49% savings) and
byte-for-byte lossless round-trip across all 12 chunks tested. The
compression code constructs this Blosc compressor explicitly rather than
relying on the installed SI version's default (see Dependencies). The codec
name is stored per row so any future codec change stays traceable. Because
compress and verify are one atomic step, every row in this table represents a
file that is both compressed and provably lossless.

No preprocessing is applied before compression. In particular, LSB correction
(`correct_lsb()`, which some groups use to squeeze better NP2 ratios) is
deliberately **not** applied: it rewrites the stored integers and would break
the byte-for-byte guarantee that is the whole point of verifying a raw
archive. The trade is a slightly lower ratio in exchange for a bit-exact
round-trip.

### OriginalDeletion (Computed)

Deletes the original binary after compression+verification has passed.
**Hard-disabled in source for v1** -- there is intentionally no easy way to
turn it on (see Safety controls). The team enables it later, deliberately,
once it agrees the time has come.

```
-> CompressedFile
---
deletion_time    : datetime
original_existed : bool      # True if file was present and deleted
```

**make() logic:**

1. Assert `CompressedFile.checksum_match` is True for this file (defensive --
   every CompressedFile row is verified by construction, so the parent's
   mere existence already implies a passed verification). If not, raise and
   refuse to delete.
2. Check whether the original file still exists on disk. If it is already
   gone (deleted manually or by a prior run), insert a row with
   `original_existed=False` and stop -- a no-op success, so re-runs are
   idempotent and never error on an already-deleted file.
3. Otherwise delete the original binary file and insert a row with
   `original_existed=True`.
4. Record the deletion timestamp.

**Safety controls (v1 deliberately makes deletion hard to enable):**

- **No shipped command.** v1 ships no `delete_originals` CLI and the
  discover/compress automation never touches this table. There is no
  command-line flag, config option, or argument a user can pass to trigger
  deletion.
- **Source-level gate.** Populating `OriginalDeletion` is guarded by a
  source-level flag (e.g. `DELETION_ENABLED = False`); while it is False the
  populate raises and refuses. Turning it on requires editing the code -- a
  deliberate, version-controlled change that is visible in git, not something
  done by normal usage. "Go into the code" is the barrier on purpose.
- **Future accessibility.** Only once the team agrees deletion should happen
  do we add a more accessible enable mechanism (a proper config/flag). Until
  that decision, the source gate stays.
- **Eligibility (once enabled).** Deletion can only act on a file that has a
  verified `CompressedFile` row (`checksum_match=True`). A file that never
  passed verification has no CompressedFile row, so deletion can never reach
  it. It also re-checks that the original still exists on disk and logs the
  deletion.
- **Requires write/delete permission on the raw store.** Deletion -- and
  compression writing the zarr alongside the original -- needs write/delete
  access to the raw ephys store, which is not a given: that store is read-only
  for some accounts by design (currently the case for the maintainer's
  account). Establishing the permission structure that allows deletion is a
  prerequisite before it is enabled. See Deployment -> Storage permissions.

### Clock files

Clock files (`*_Clock*.bin`) contain uint64 ONIX timestamps. They are small
(a few MB each) and are not compressed by this pipeline. They remain
alongside the zarr directories on Ceph. No table or processing for clock
files.

---

## File discovery

Discovery runs inside `RawEphysDiscovery.make()`, scoped by the trigger that
spawned it. The `discovery.py` module does the filesystem work.

### Scan logic

For a given trigger, start from its `experiment_path` (optionally narrowed to
a single `epoch_path`):

1. Find all epoch directories under the trigger's scope (ISO timestamp
   directory names); if `epoch_path` is set, restrict to that one.
2. Within each epoch, find device directories containing
   `*_AmplifierData*.bin` files.
3. For each device directory, read `Metadata.yml` from the epoch directory
   to determine probe configuration (channel count, sampling rate).
4. Insert one `RawEphysFile` part row per *complete* binary file that is not
   already registered.

### Metadata.yml parsing

The channel count and sampling rate come from `Metadata.yml` in the epoch
directory. **Despite the `.yml` extension this file is JSON**, so it is parsed
with the standard-library `json` module (not a YAML parser). The relevant
fields are the probe's channel count (number of active channels, typically 384
for NP2) and the sampling frequency (30000 Hz for NP2). Discovery reads these
directly without importing aeon_mecha.

**Watch the layout variants.** Metadata.yml is not uniform and has caused
parsing bugs before:

- Probe config lives at `["Devices"]["NeuropixelsV2e"]["ConfigurationA/B"]`,
  not at a top level.
- The **device name differs between filesystem and metadata**: the directory
  is `NeuropixelsV2` while the metadata key is `NeuropixelsV2e`. The parser
  must bridge this.
- **V2 vs V2Beta** layouts differ (e.g. V2Beta has no serial numbers; V2
  stores a serial in a Windows-format path). The parser must handle both.

The implementation **reimplements** aeon_mecha's known parsing rules rather
than importing them, to stay decoupled (team-accepted). The cost is that this
parser can drift from aeon_mecha's if their Metadata.yml handling changes; that
is an accepted trade for keeping the library usable without aeon_mecha.

### File completeness

Discovery must only register **complete** files: raw binaries are written
incrementally during acquisition, and compressing one that is still being
appended to would silently corrupt the result. There is **no completion
marker** to rely on -- the Bonsai/ONIX acquisition writes no end-of-epoch
sentinel or manifest, and `Metadata.yml` is written once at recording *start*,
not stop. Completeness has to be inferred from the chunk files themselves.

How acquisition writes the chunks (confirmed from the Bonsai writer source):
the continuous data stream is cut into fixed-count windows, and each numbered
chunk `{...}_AmplifierData_{N}.bin` is written by a separate file handle that
opens when chunk N begins and is **closed at the exact moment chunk `_{N+1}`
is created**. Every chunk is constant-size by construction except the last,
which is shorter and is closed only when the recording stops. This gives a
hard primary rule plus a fallback for the final chunk:

- **Primary -- successor exists.** Chunk `_N` is complete once `_{N+1}` exists
  in the same device directory. This follows directly from the writer
  mechanics and covers every chunk except the highest-numbered one. (Treat a
  gap in the numbering as an anomaly to flag.)
- **Final (highest-numbered) chunk -- no successor.** Register it only when the
  epoch is known finished AND the file is quiescent. "Epoch finished" is
  established out-of-band: a newer epoch directory (later ISO timestamp)
  exists, or the acquisition process is no longer running. Quiescent = size and
  mtime unchanged across a long threshold (well over one chunk's duration --
  conservative, because the handle is held open for the whole chunk and Ceph
  may not flush mtime promptly). For the very last recording a rig produces
  there is no newer epoch directory, so the quiescence threshold (with the
  acquisition process gone) is what eventually brings that final chunk in -- it
  is picked up on a later scan once it has been stable long enough, rather than
  falling through the cracks.

A flat `.bin` has no footer or index, so a crashed/truncated chunk is
indistinguishable from a complete one by inspection -- only the successor rule
actually proves closure, which is why the final chunk leans on the
epoch-finished guard. An expected-constant-size check can corroborate
completeness for non-final chunks but cannot deny it for the legitimately
short last chunk.

This matters now for manual runs, and is load-bearing for the unattended
nightly/cron runs (see Deployment -> Common properties): a job that fires
mid-recording must not pick up a half-written binary.

### Idempotency

Re-running discovery is safe. A new trigger over an overlapping directory
re-encounters files already registered under an earlier trigger; make() skips
any file already present in the `RawEphysFile` registry, so each physical
file is registered (and later compressed) exactly once. Placing the same
trigger twice simply yields a second, near-empty discovery run.

### Discovery source

Discovery is a **standalone filesystem scan** (above). Reading the raw-file
set from aeon_mecha's epoch-discovery table instead was considered, but the
standalone scan was chosen: it keeps the library usable without aeon_mecha --
including for users who do not process through DataJoint at all -- and avoids
any dependency on the analysis pipeline.

---

## Companion aeon_mecha PR (required)

Once raw files are compressed, aeon_mecha must be able to **find and read**
the compressed zarr files. This is a required companion deliverable, in a
separate aeon_mecha PR. The primary consumer is `PreProcessing.make_compute`,
which currently reads raw binaries via `read_binary()`.

(For context: aeon_mecha PR **#589**, in draft, moves *intermediate* files to
zarr compression. The raw-file read support here is a separate concern and is
its own PR, not part of #589.)

The changes live in aeon_mecha, not in this library. The design (settled in
the 2026-06-25 meeting) is **store-as-read + resolve-at-load**, and treats
reading uncompressed binary as a first-class case -- some users will never
compress their data -- not merely a fallback:

- **A recording is identified by its stem** and may exist as `.zarr`
  (preferred), `.bin`, or both -- same stem, different extension.
- **Store the path exactly as read; never edit it (insert-only).**
  `EphysChunk.File` today stores one row per chunk file with `file_path`
  *including* its extension (currently always `.bin`). Under the new design it
  stores whatever was actually read at processing time (`.bin` or `.zarr`) and
  the row is never rewritten -- it is NOT updated to point at the `.zarr`
  later, and the path is NOT stored extensionless. The stored path stays a
  faithful record of what was read.
- **A single pure resolver utility at load time.** Given a stored path, it
  derives the stem, checks Ceph for the `.zarr` of that stem, and returns the
  `.zarr` if present (preferred) else the `.bin`. Pure lookup: no side effects,
  never writes back to the table. `PreProcessing` reads whatever it returns.
- **Discovery must find either format.** `ingest_chunks` today globs
  `*_AmplifierData*.bin`; it must also discover recordings that exist only as
  `.zarr` (because their `.bin` was deleted), matching by stem.

Why store-as-read and resolve every time (option A): a stored path can go stale
regardless -- e.g. processing reads and records a `.bin` before compression
runs, then the compression pipeline writes the `.zarr` and deletes the `.bin`,
so the stored `.bin` now points at a file that is gone. The resolver always
re-checks Ceph rather than trusting the stored path, so editing the row would
add nothing and the insert-only record stays faithful. This relies on the
compression-side contract that the original `.bin` is deleted only after the
verified `.zarr` exists with the same stem (see CompressedFile / OriginalDeletion).

**Verifying this connection is part of this work's tests:** that a compressed
file is discovered and read correctly by the downstream pipeline. Compression
and verification can run safely before this PR lands, but it is a hard
prerequisite for enabling `OriginalDeletion` -- deleting a `.bin` before
PreProcessing can read the corresponding `.zarr` would leave the sorting
pipeline with no input.

**Mid-session / unfinished chunks (deferred).** Because compression leaves an
in-progress final chunk uncompressed until it is complete, a recording can
transiently hold a mix of `.zarr` (finished chunks) and `.bin` (the latest,
still-growing chunk). v1 is built to be *safe* about this -- it simply never
registers an incomplete chunk, so it cannot corrupt anything -- but it does
not try to solve smarter handling of partially-compressed recordings in
aeon_mecha. Whether aeon_mecha needs added logic for that mix is left until
after the compression pipeline is implemented and reviewed, and is a
team-approved follow-up rather than part of this work.

---

## Open Ephys native compression (future)

Open Ephys / Bonsai ONIX1 supports writing compressed files at acquisition
time via the DataFrame writer. If adopted, this would eliminate the need for
post-hoc raw compression entirely. The current blocker is that
SpikeInterface cannot natively read these compressed files. An incoming team
member who also maintains SpikeInterface part-time will raise native support
for this format with the SI team.

If native compression becomes available, this standalone pipeline would
still be useful for compressing historical data that was acquired before the
switch.

---

## Deployment

Two deployment modes, both served by the same library. The per-user mode is
the v1 rollout; the centralized mode is a later option.

### Mode 1 -- per-user submodule (v1)

Each experimenter adds this library as a submodule next to aeon_mecha in their
analysis repo and runs it on their own raw data, on their own HPC compute
quota. Typical workflow:

1. Place a trigger for the directories to compress, attributed to you:
   `add_trigger.py --experiment AEONX1/<exp> [--epoch <epoch>] --placed-by <name>`.
2. Run `run.py`, which populates `RawEphysDiscovery` (scan, scoped by the
   trigger) -> `CompressedFile` (compress + verify). Restricting populate to
   your own triggers (by `placed_by`) keeps you working only on your data.

Deletion of originals is not part of this workflow in v1: it is hard-disabled
in source and has no shipped command (see OriginalDeletion).

A **SLURM nightly template** (`templates/nightly_compress.sbatch`) is part of
the deliverable: a job scheduled overnight that compresses that day's
recordings, so there is no single huge compression step at the end of a
multi-day recording. Setup instructions live in `templates/README.md`.

Compression is GPU-free and only modestly CPU/memory-bound (see Resource
requirements), so SLURM is used here for scheduling and quota accounting, not
because the work needs a GPU.

### Mode 2 -- centralized service (later option)

The same library can run as a simple cron on the DataJoint database server
(the persistent server that hosts `aeondj`, which DataJoint operates). With no
GPU requirement it needs no SLURM at all: the cron periodically runs `run.py`
over whatever triggers exist and sits idle when there are none. This
centralizes execution once the team is confident. It implies the shared-schema
migration noted under Database (porting per-project records into a common
schema); the library code itself needs no change, and per-trigger attribution
records who requested each run.

### Common properties

Every step is idempotent -- discovery skips already-registered files and each
`.populate()` only fills missing keys -- so re-running picks up new work and
retries previously errored files without redoing completed ones. The
compress+verify step is the expensive one (~15 min compress + ~4 min verify
per hour of recording per shank, I/O bound).

**Concurrency.** Within a project, multiple workers can run at once (e.g. a
SLURM array, or a nightly cron overlapping a manual run). All `populate()`
calls use DJ job reservation (`reserve_jobs=True`) so two workers never pick
the same key; combined with the `unique index (file_path)` on the registry, no
file is discovered or compressed twice.

Requirements for unattended (nightly/cron) operation:

- **File completeness** -- discovery registers only complete files (see File
  discovery -> File completeness). A job firing mid-recording must not pick up
  a binary that is still being written.
- **Deletion gating** -- `OriginalDeletion` is hard-disabled in source (see
  OriginalDeletion) and the automation never touches it. It is enabled only
  by a deliberate code change, after the companion aeon_mecha PR lands
  (PreProcessing reads zarr) and the team agrees. Until then the automation
  runs discovery + compress+verify only.

### Resource requirements

Based on compression testing results:

- **CPU:** 2-4 cores sufficient (compression is I/O bound on Ceph, not CPU
  bound)
- **Memory:** 8 GB sufficient for compression and verification
- **GPU:** Not needed
- **Time:** ~15 min compression + ~4 min verification per hour of recording
  per shank

**Set `n_jobs` deliberately under SLURM.** `recording.save(format="zarr")`
parallelizes by `n_jobs`. On SLURM, `os.cpu_count()` reports all physical
cores on the node, not the cgroup allocation, so a fractional `n_jobs` (e.g.
`0.8`) over-subscribes and spawns far more workers than the job was granted.
There has also been an intermittent fork+BLAS crash (`BrokenProcessPool`) on
this HPC, for which `n_jobs=1` is the known-safe fallback. The nightly SLURM
template must set `n_jobs` to the actual allocation explicitly rather than
auto-scale.

### Storage permissions

Two of the pipeline's actions write to the raw ephys store: compression writes
the zarr **alongside** the original, and `OriginalDeletion` removes the
original. Both require write/delete permission on that store -- which is not a
given. The raw ephys store is **read-only for some accounts by design**
(currently the case for the maintainer's account). Consequences:

- **Production:** a permission structure granting the running account write
  (for zarr output) and, later, delete (for `OriginalDeletion`) on the raw
  store must be established. This is a prerequisite before enabling deletion,
  and before any centralized run that writes into another user's data area. If
  write-on-the-raw-store is never granted, the alternative is to emit zarr to a
  separate writable tree -- at the cost of co-location, which the companion
  aeon_mecha read path currently assumes.
- **Development/testing:** the write (compress) and delete paths are exercised
  on a **copy** of a data file placed in a writable store, not against the
  read-only raw originals.

---

## Testing

Unit tests using synthetic data (small binary files created in a temp
directory). Integration tests using a subset of the golden dataset on the
HPC.

Test coverage:

- A trigger scopes discovery to its directories (files outside scope are not
  registered); `placed_by` filtering selects only that user's work
- Discovery correctly finds and parses AmplifierData files
- Discovery skips incomplete / still-being-written files
- Discovery skips files already registered (each physical file once, even
  across overlapping triggers)
- Compression produces valid zarr that loads via SI
- Compression's built-in verification catches intentional data corruption
  (flip a bit, truncate) -- `make()` raises and inserts no row
- Deletion refuses to run when no verified `CompressedFile` row exists; when
  the original is already gone it inserts `original_existed=False` (idempotent
  no-op) rather than erroring
- The compress-write and delete paths are exercised on a copy of a data file
  in a writable store (the raw originals are read-only)
- Idempotent re-runs of each step
- **Downstream connection:** a compressed zarr file is discovered and read
  correctly by aeon_mecha PreProcessing (validates the companion PR)

---

## Decided (from review + the 2026-06-18 meeting)

- **Repo home:** `aeon_raw_compression` in the SainsburyWellcomeCentre org,
  starting **private** (made public later, once team-approved). Remote not yet
  created.
- **Execution model:** standalone library, per-user submodule first;
  centralized DB-server cron a later option. v1 stores its tables under each
  project's own prefix on `aeondj` (per-project tracking); a shared/central
  schema is deferred and would require porting per-project records.
- **Discovery:** driven by a scoped, attributed trigger table, using a
  standalone filesystem scan (not the aeon_mecha epoch table), so the library
  has no dependency on the analysis pipeline.
- **File completeness:** a chunk is complete once its successor `_{N+1}` exists
  (a hard guarantee from the Bonsai writer, which closes chunk N as it opens
  N+1); the final, highest-numbered chunk is gated on the epoch being finished
  plus file quiescence. No acquisition completion marker exists to rely on.
- **File uniqueness:** enforced at the database level by `unique index
  (file_path)` on the `RawEphysFile` part, backed up by make() skipping
  already-registered files. "Compress once" is a hard, race-proof guarantee.
- **Automation:** a SLURM nightly template ships with the library; the
  centralized cron is a later option that needs no SLURM.
- **Companion aeon_mecha PR** to read compressed files is required and is
  in-scope for this work's tests.
- **Deletion:** hard-disabled in source for v1 -- no shipped command; enabling
  requires a deliberate, version-controlled code change. The team decides
  *when* to enable it; only then is a more accessible mechanism added.

## Open questions

1. **File-completeness implementation details.** The approach is decided (see
   File discovery -> File completeness): the primary "successor chunk exists"
   rule, plus an epoch-finished + quiescence guard for the final chunk. Still
   to pin down during build: the exact quiescence threshold; how "epoch
   finished" is detected in practice (newer-epoch-directory vs. an
   acquisition-process check); and the production chunk count/duration (the
   writer's window size, confirmed only for the foragingABC repo, not
   production). Inspecting the current Ceph data will settle these.
   (When to *enable deletion* is a deferred team decision, not an open spec
   question.)
2. **Raw-store write/delete permissions.** The raw ephys store is read-only
   for some accounts by design, but the pipeline must write the zarr there
   (and, eventually, delete originals). The permission structure that grants
   the running account write -- and later delete -- on the raw store is not
   yet worked out; it is a prerequisite for production write/delete (see
   Deployment -> Storage permissions).
