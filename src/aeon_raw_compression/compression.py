"""Compress a raw AmplifierData binary to zarr and verify a byte-exact round-trip.

Pure SpikeInterface/zarr logic, DB-free -- the DataJoint layer calls these in
``make()``. Compression and verification are deliberately separate functions
but are invoked as one atomic step by the pipeline: a row is only inserted once
both succeed.

The codec is the exact configuration validated on ``es/compression-spec``:
``Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE)`` -- recorded as
``"blosc-zstd-5-bitshuffle"`` -- which produced the 1.95x mean ratio and a
byte-for-byte lossless round-trip. It is constructed **explicitly** rather than
relying on the installed SpikeInterface version's default zarr compressor, so
the codec stays fixed even if SI's default changes.

No preprocessing is applied before compression. LSB correction
(``correct_lsb()``) is deliberately NOT used: it rewrites the stored integers
and would break the byte-for-byte guarantee that is the point of a verified raw
archive.
"""

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numcodecs import Blosc

from aeon_raw_compression.metadata import NP2_DTYPE

CODEC_NAME = "blosc-zstd-5-bitshuffle"

# Explicit n_jobs (never rely on SpikeInterface's global default). 1 is safe on
# SLURM: it avoids the os.cpu_count() oversubscription trap and the intermittent
# fork+BLAS crash on this HPC. Override via env for a deliberately parallel run.
DEFAULT_N_JOBS = int(os.environ.get("AEON_RAW_COMPRESSION_N_JOBS", "1"))

# Zarr chunk duration (seconds). Zarr stores each array as a directory of many
# small chunk files; the file count per recording is ~ recording_seconds /
# chunk_duration_s. Larger chunks => far fewer files (kinder to CephFS) at the
# cost of a larger minimum read. This is the ONLY file-count lever we have on
# zarr 2 (zarr-3 sharding would be better but needs SpikeInterface zarr-3
# support, which doesn't exist yet -- see SpikeInterface issue #4014).
# Default 30 s: measured 26 files per 13.8 GB (10-min) chunk on real 384-ch data
# (~690 MB/chunk in memory), so a 50 TB *compressed* project is ~183k files and
# tens of such projects stay in the low millions -- well inside CephFS comfort,
# while keeping inspection reads modest. Env-overridable; tune on Ceph.
DEFAULT_CHUNK_DURATION_S = float(os.environ.get("AEON_RAW_COMPRESSION_CHUNK_DURATION_S", "30"))

# Samples per block for the memory-bounded round-trip compare. Large enough to
# keep overhead low, small enough that a 30 GB chunk never loads at once.
_VERIFY_CHUNK_SAMPLES = 100_000

# Sequential read size for hashing the original .bin (memory-bounded).
_HASH_READ_BYTES = 64 * 1024 * 1024  # 64 MiB


class VerificationError(Exception):
    """Raised when the zarr round-trip does not match the original byte-for-byte."""


@dataclass(frozen=True)
class CompressionResult:
    zarr_path: str
    compressed_size_bytes: int
    compression_ratio: float
    compression_time_s: float
    codec_name: str
    num_samples: int
    content_hash: str  # sha256 hex of the original .bin bytes (durable digest)


@dataclass(frozen=True)
class VerifyResult:
    decompression_time_s: float
    num_samples: int
    checksum_match: bool  # always True when returned -- a mismatch raises instead


def _blosc_compressor() -> Blosc:
    return Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE)


def _read_binary(bin_path, num_channels, sampling_frequency, dtype):
    import spikeinterface.extractors as se

    return se.read_binary(
        file_paths=str(bin_path),
        sampling_frequency=float(sampling_frequency),
        dtype=dtype,
        num_channels=int(num_channels),
    )


def compress_to_zarr(
    bin_path,
    zarr_path,
    num_channels,
    sampling_frequency,
    dtype=NP2_DTYPE,
    n_jobs=DEFAULT_N_JOBS,
    chunk_duration_s=DEFAULT_CHUNK_DURATION_S,
) -> CompressionResult:
    """Compress ``bin_path`` to a zarr directory at ``zarr_path``.

    Removes any stale/partial zarr already at ``zarr_path`` first (a prior run
    killed mid-write leaves a directory but no row; ``recording.save`` errors if
    the folder exists, which would wedge the file in ``jobs.errors``). Saves
    with the explicit Blosc codec and returns size/ratio/timing metrics.

    ``chunk_duration_s`` sets the zarr time-chunk size (forwarded to SI as an
    ``"<n>s"`` duration string); larger values mean fewer on-disk chunk files.
    """
    bin_path = Path(bin_path)
    zarr_path = Path(zarr_path)

    if zarr_path.exists():
        shutil.rmtree(zarr_path)

    recording = _read_binary(bin_path, num_channels, sampling_frequency, dtype)
    num_samples = int(recording.get_num_samples())

    start = time.perf_counter()
    recording.save(
        format="zarr",
        folder=str(zarr_path),
        compressor=_blosc_compressor(),
        n_jobs=int(n_jobs),
        chunk_duration=f"{chunk_duration_s:g}s",
        progress_bar=False,
    )
    compression_time_s = time.perf_counter() - start

    compressed_size = _directory_size(zarr_path)
    original_size = bin_path.stat().st_size
    ratio = original_size / compressed_size if compressed_size else 0.0

    # Durable digest of the original bytes: lets a future tool confirm a .zarr
    # still decodes to the original even after the original is deleted, without
    # needing the original present. One extra sequential read, cheap vs the SI
    # read+write above.
    content_hash = _sha256_of_file(bin_path)

    return CompressionResult(
        zarr_path=zarr_path.resolve().as_posix(),
        compressed_size_bytes=compressed_size,
        compression_ratio=ratio,
        compression_time_s=compression_time_s,
        codec_name=CODEC_NAME,
        num_samples=num_samples,
        content_hash=content_hash,
    )


def verify_roundtrip(
    bin_path, zarr_path, num_channels, sampling_frequency, dtype=NP2_DTYPE
) -> VerifyResult:
    """Verify the zarr archive reproduces the original byte-for-byte.

    Compares sample counts, then the trace data block-by-block (memory-bounded).
    Raises :class:`VerificationError` on any difference -- the caller must let
    that propagate so no CompressedFile row is inserted for an unverified file.
    """
    import spikeinterface as si

    bin_path = Path(bin_path)
    zarr_path = Path(zarr_path)

    original = _read_binary(bin_path, num_channels, sampling_frequency, dtype)

    start = time.perf_counter()
    compressed = si.load(str(zarr_path))

    n_original = int(original.get_num_samples())
    n_compressed = int(compressed.get_num_samples())
    if n_original != n_compressed:
        raise VerificationError(
            f"sample-count mismatch: original {n_original} != zarr {n_compressed} ({bin_path.name})"
        )

    for block_start in range(0, n_original, _VERIFY_CHUNK_SAMPLES):
        block_end = min(block_start + _VERIFY_CHUNK_SAMPLES, n_original)
        original_block = original.get_traces(start_frame=block_start, end_frame=block_end)
        compressed_block = compressed.get_traces(start_frame=block_start, end_frame=block_end)
        if not np.array_equal(original_block, compressed_block):
            raise VerificationError(
                f"data mismatch in samples [{block_start}:{block_end}) ({bin_path.name})"
            )

    decompression_time_s = time.perf_counter() - start
    return VerifyResult(
        decompression_time_s=decompression_time_s,
        num_samples=n_original,
        checksum_match=True,
    )


def _directory_size(path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def _sha256_of_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_HASH_READ_BYTES), b""):
            h.update(block)
    return h.hexdigest()
