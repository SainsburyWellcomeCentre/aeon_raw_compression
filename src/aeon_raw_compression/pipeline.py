"""DataJoint tables for the raw-ephys compression pipeline.

Four tables, thin ``make()`` wrappers over the pure modules:

* ``RawEphysDiscoveryTrigger`` (Manual) -- scopes a discovery run + records who
  placed it.
* ``RawEphysDiscovery`` (Imported) + ``RawEphysFile`` (Part) -- scans for
  complete raw files and registers one part row per file.
* ``CompressedFile`` (Computed) -- compresses to zarr AND verifies the
  round-trip in one atomic step; a row exists only if both passed.
* ``RawEphysFileDeletion`` (Computed) -- deletes the original raw file; the
  automated path is **hard-disabled in source** for v1
  (``DELETION_ENABLED = False``). The shipped workflow is the read-only
  :func:`report_deletable` green-light plus manual deletion.

The schema uses **deferred activation**: importing this module does not open a
database connection. Call :func:`activate` (with the project's own DataJoint
prefix) before populating. The tables live under the project's own prefix --
there is no separate compression schema (per-project tracking for v1).
"""

import datetime
import shutil
from pathlib import Path

import datajoint as dj

from aeon_raw_compression.compression import (
    VerificationError,
    compress_to_zarr,
    verify_roundtrip,
)
from aeon_raw_compression.discovery import discover_raw_files

# --- Safety: deletion is hard-disabled in source for v1 --------------------
# Enabling deletion is a deliberate, version-controlled change visible in git,
# not something normal usage can trigger. "Go into the code" is the barrier on
# purpose. Do NOT flip this without the team's explicit decision and a
# write/delete-capable raw store (see spec: Safety controls).
DELETION_ENABLED = False

# Default Ceph raw-data root. A relative trigger ``experiment_path`` (e.g.
# "AEONX1/abcGolden01") is resolved against this; an absolute experiment_path is
# used as-is (handy for tests). Override via :func:`activate`.
DEFAULT_RAW_DATA_ROOT = "/ceph/aeon/aeon/data/raw"
RAW_DATA_ROOT = DEFAULT_RAW_DATA_ROOT

# Default Ceph processed-data root. The compressed zarr is written here (NOT
# beside the raw .bin), mirroring the raw sub-path, so the raw store can stay
# read-only. CONFIRM the exact path on Ceph before production use. Override via
# :func:`activate`.
DEFAULT_PROCESSED_DATA_ROOT = "/ceph/aeon/aeon/data/processed"
PROCESSED_DATA_ROOT = DEFAULT_PROCESSED_DATA_ROOT

# Deferred schema -- not activated until activate() is called.
schema = dj.Schema()


@schema
class RawEphysDiscoveryTrigger(dj.Manual):
    definition = """
    # A request to scan specific directories for raw ephys files to compress.
    trigger_time     : datetime      # when the trigger was placed (part of PK)
    placed_by        : varchar(64)   # who placed it -- audit / future migration
    ---
    experiment_path  : varchar(255)  # directory in scope, e.g. "AEONX1/abcGolden01"
    epoch_path=''    : varchar(255)  # optional: restrict to one epoch; '' = all
    """


@schema
class RawEphysDiscovery(dj.Imported):
    definition = """
    # One discovery scan for a trigger; registers the raw files found.
    -> RawEphysDiscoveryTrigger
    ---
    num_files_found  : int32         # new raw files registered by this run
    num_anomalies    : int32         # gaps / unparseable names seen this run
    anomalies=''     : varchar(2047) # joined anomaly messages (truncated)
    discovery_time   : datetime
    """

    class RawEphysFile(dj.Part):
        definition = """
        # Registry of raw files eligible for compression (one row per file).
        -> master
        file_path          : varchar(512)  # absolute path on Ceph (part of PK)
        ---
        experiment_path    : varchar(255)
        epoch_dir          : varchar(64)
        device_name        : varchar(64)
        probe_label        : varchar(32)
        file_name          : varchar(128)
        file_size_bytes    : int64
        num_channels       : int32
        sampling_frequency : float64
        unique index (file_path)        # one row per physical file across all triggers
        """

    def make(self, key):
        trigger = (RawEphysDiscoveryTrigger & key).fetch1()
        experiment_dir = _resolve_experiment_dir(trigger["experiment_path"])
        epoch_path = trigger["epoch_path"] or None

        # Registry-wide dedup: skip files already registered by any trigger.
        already_registered = set(RawEphysDiscovery.RawEphysFile.to_arrays("file_path"))

        # Collect anomalies (numbering gaps, unparseable names) so they are
        # persisted on the row, not just logged where they scroll past in a
        # SLURM .out.
        anomalies = []
        records = discover_raw_files(
            experiment_dir,
            experiment_path=trigger["experiment_path"],
            epoch_path=epoch_path,
            already_registered=already_registered,
            on_anomaly=anomalies.append,
        )

        self.insert1(
            {
                **key,
                "num_files_found": len(records),
                "num_anomalies": len(anomalies),
                "anomalies": "; ".join(anomalies)[:2047],
                "discovery_time": datetime.datetime.now(),
            }
        )
        # skip_duplicates: under concurrent overlapping triggers, two runs can
        # race past the in-Python already_registered dedup and collide on the
        # unique index (file_path). Skipping the colliding rows lets the loser
        # finish instead of erroring the whole discovery run into jobs.errors.
        # num_files_found above is the count this run INTENDED to add (an upper
        # bound; a few may be skipped by the index under that race).
        self.RawEphysFile.insert(
            (
                {
                    **key,
                    "file_path": r.file_path,
                    "experiment_path": r.experiment_path,
                    "epoch_dir": r.epoch_dir,
                    "device_name": r.device_name,
                    "probe_label": r.probe_label,
                    "file_name": r.file_name,
                    "file_size_bytes": r.file_size_bytes,
                    "num_channels": r.num_channels,
                    "sampling_frequency": r.sampling_frequency,
                }
                for r in records
            ),
            skip_duplicates=True,
        )


@schema
class CompressedFile(dj.Computed):
    definition = """
    # Compress a raw file to zarr and verify a byte-exact round-trip (atomic).
    -> RawEphysDiscovery.RawEphysFile
    ---
    zarr_path             : varchar(512)  # zarr dir under the processed root (same stem as .bin)
    compressed_size_bytes : int64
    compression_ratio     : float64       # original / compressed
    compression_time_s    : float64
    decompression_time_s  : float64
    codec_name            : varchar(64)   # e.g. "blosc-zstd-5-bitshuffle"
    checksum_match        : bool          # round-trip compare result (True for every row)
    content_hash          : char(64)      # sha256 hex of the original .bin bytes (durable digest)
    num_samples           : int64
    execution_time        : datetime
    """

    def make(self, key):
        raw = (RawEphysDiscovery.RawEphysFile & key).fetch1()
        bin_path = Path(raw["file_path"])
        # Write the zarr under the processed root (mirroring the raw sub-path),
        # never beside the read-only raw .bin.
        zarr_path = _processed_zarr_path(bin_path)
        zarr_path.parent.mkdir(parents=True, exist_ok=True)

        result = compress_to_zarr(
            bin_path, zarr_path, raw["num_channels"], raw["sampling_frequency"]
        )
        try:
            verification = verify_roundtrip(
                bin_path, zarr_path, raw["num_channels"], raw["sampling_frequency"]
            )
        except VerificationError:
            # Untrusted archive: remove it so a retry starts clean, then re-raise
            # so no row is inserted (the failure lands in jobs.errors).
            if zarr_path.exists():
                shutil.rmtree(zarr_path)
            raise

        self.insert1(
            {
                **key,
                "zarr_path": result.zarr_path,
                "compressed_size_bytes": result.compressed_size_bytes,
                "compression_ratio": result.compression_ratio,
                "compression_time_s": result.compression_time_s,
                "decompression_time_s": verification.decompression_time_s,
                "codec_name": result.codec_name,
                "checksum_match": verification.checksum_match,
                "content_hash": result.content_hash,
                "num_samples": verification.num_samples,
                "execution_time": datetime.datetime.now(),
            }
        )


@schema
class RawEphysFileDeletion(dj.Computed):
    definition = """
    # Deletes the original raw file after compression+verification.
    # Automated path DISABLED in v1 (DELETION_ENABLED=False); the shipped
    # workflow is the read-only report_deletable() green-light + manual delete.
    -> CompressedFile
    ---
    deletion_time        : datetime
    original_existed     : bool          # True if the file was present and deleted
    deletion_mode='auto' : varchar(16)   # 'auto' (pipeline unlinked) | 'manual'
    """

    def make(self, key):
        if not DELETION_ENABLED:
            raise RuntimeError(
                "RawEphysFileDeletion is disabled (DELETION_ENABLED is False). "
                "Enabling automated deletion is a deliberate, version-controlled "
                "source change and requires a write/delete-capable raw store. The "
                "shipped workflow is report_deletable() + manual deletion. "
                "Refusing to delete."
            )

        compressed = (CompressedFile & key).fetch1()
        if not compressed["checksum_match"]:
            # Defensive: every CompressedFile row is verified by construction.
            raise RuntimeError(f"refusing to delete {key}: CompressedFile.checksum_match is False")

        raw = (RawEphysDiscovery.RawEphysFile & key).fetch1()
        bin_path = Path(raw["file_path"])
        original_existed = bin_path.exists()
        if original_existed:
            bin_path.unlink()

        self.insert1(
            {
                **key,
                "deletion_time": datetime.datetime.now(),
                "original_existed": original_existed,
                "deletion_mode": "auto",
            }
        )


def report_deletable(restriction=None):
    """Verified-compressed raw files that still exist and are not yet actioned.

    The read-only "green-light" list for manual deletion: every verified
    ``CompressedFile`` whose raw ``.bin`` is still present and has no
    :class:`RawEphysFileDeletion` record. Writes nothing. The recording computer
    (the only machine with Ceph delete rights) deletes these paths manually.

    Returns a list of ``{"file_path", "file_size_bytes"}`` dicts.
    """
    restriction = restriction or {}
    pending = CompressedFile - RawEphysFileDeletion  # compressed, not yet actioned
    files = (RawEphysDiscovery.RawEphysFile & pending & restriction).to_dicts()
    return [
        {"file_path": f["file_path"], "file_size_bytes": f["file_size_bytes"]}
        for f in files
        if Path(f["file_path"]).exists()
    ]


def activate(
    prefix=None,
    *,
    raw_data_root=None,
    processed_data_root=None,
    create_schema=True,
    create_tables=True,
):
    """Activate the compression schema under the project's own DataJoint prefix.

    Args:
        prefix: DataJoint prefix to host the tables under. Defaults to the
            project's configured prefix (``dj.config.database.database_prefix``).
            The schema name is ``f"{prefix}_aeon_raw_compression"``.
        raw_data_root: Optional override for the Ceph raw-data root used to
            resolve relative trigger ``experiment_path`` values.
        processed_data_root: Optional override for the Ceph processed-data root
            the compressed zarr is written under.
        create_schema, create_tables: Passed through to ``schema.activate``.
    """
    global RAW_DATA_ROOT, PROCESSED_DATA_ROOT
    if raw_data_root is not None:
        RAW_DATA_ROOT = str(raw_data_root)
    if processed_data_root is not None:
        PROCESSED_DATA_ROOT = str(processed_data_root)
    if prefix is None:
        prefix = dj.config.database.database_prefix
    schema.activate(
        _schema_name(prefix),
        create_schema=create_schema,
        create_tables=create_tables,
    )


def _schema_name(prefix: str) -> str:
    """Schema name from a DataJoint prefix, with exactly one separating ``_``.

    Project prefixes conventionally end in ``_`` (e.g.
    ``u_elissas_aeon_ephys_v2_test_``); naive concatenation would double the
    underscore. Strip any trailing ``_`` then append ``_aeon_raw_compression``.
    """
    return f"{prefix.rstrip('_')}_aeon_raw_compression"


def _resolve_experiment_dir(experiment_path):
    """Resolve a trigger ``experiment_path`` to an absolute directory.

    Absolute paths are used as-is (useful for tests writing to a temp dir);
    relative paths are resolved against :data:`RAW_DATA_ROOT`.
    """
    path = Path(experiment_path)
    return path if path.is_absolute() else Path(RAW_DATA_ROOT) / path


def _processed_zarr_path(bin_path):
    """Re-root a raw ``.bin`` path under :data:`PROCESSED_DATA_ROOT`, as a ``.zarr``.

    Mirrors the raw sub-path so the read-side resolver (companion aeon_mecha PR)
    can find the zarr by the same relative path under the processed root. The
    zarr is written to the processed store, never beside the read-only raw file.
    """
    bin_path = Path(bin_path)
    raw_root = Path(RAW_DATA_ROOT)
    try:
        rel = bin_path.relative_to(raw_root)
    except ValueError as e:
        raise ValueError(
            f"{bin_path} is not under RAW_DATA_ROOT ({raw_root}); cannot map to processed"
        ) from e
    return (Path(PROCESSED_DATA_ROOT) / rel).with_suffix(".zarr")
