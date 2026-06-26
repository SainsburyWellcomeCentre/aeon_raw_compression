"""Unit tests for aeon_raw_compression.compression.

Exercises the lossless round-trip, the explicit codec name, a meaningful
compression ratio, stale-zarr cleanup, and both verification-failure branches
(sample-count and data mismatch). The mismatches are induced by changing the
*original* after compression -- the deterministic way to drive verify's
comparison-and-raise logic (a corrupt archive hits the same comparison branch).
"""

import numpy as np
import pytest

from aeon_raw_compression.compression import (
    CODEC_NAME,
    VerificationError,
    compress_to_zarr,
    verify_roundtrip,
)


def _write_bin(path, n_samples=2000, n_channels=8, seed=0):
    rng = np.random.default_rng(seed)
    ramp = np.arange(n_samples, dtype=np.int64)[:, None] % 4000
    noise = rng.integers(-3, 4, size=(n_samples, n_channels))
    data = ((ramp + noise) % 4000).astype(np.uint16)
    data.tofile(path)
    return data


def test_roundtrip_is_lossless(tmp_path):
    b = tmp_path / "Dev_ProbeA_AmplifierData_0.bin"
    _write_bin(b)
    z = tmp_path / "Dev_ProbeA_AmplifierData_0.zarr"

    res = compress_to_zarr(b, z, num_channels=8, sampling_frequency=30000)
    assert res.codec_name == CODEC_NAME == "blosc-zstd-5-bitshuffle"
    assert res.num_samples == 2000
    assert res.compressed_size_bytes > 0
    assert res.compression_ratio > 1.0

    v = verify_roundtrip(b, z, num_channels=8, sampling_frequency=30000)
    assert v.num_samples == 2000
    assert v.checksum_match is True


def test_verify_detects_data_mismatch(tmp_path):
    b = tmp_path / "Dev_ProbeA_AmplifierData_0.bin"
    data = _write_bin(b)
    z = tmp_path / "Dev_ProbeA_AmplifierData_0.zarr"
    compress_to_zarr(b, z, num_channels=8, sampling_frequency=30000)

    corrupt = data.copy()
    corrupt[1000] = (corrupt[1000] + 1) % 4000  # same size, different values
    corrupt.astype(np.uint16).tofile(b)

    with pytest.raises(VerificationError):
        verify_roundtrip(b, z, num_channels=8, sampling_frequency=30000)


def test_verify_detects_sample_count_mismatch(tmp_path):
    b = tmp_path / "Dev_ProbeA_AmplifierData_0.bin"
    data = _write_bin(b)
    z = tmp_path / "Dev_ProbeA_AmplifierData_0.zarr"
    compress_to_zarr(b, z, num_channels=8, sampling_frequency=30000)

    data[:-100].astype(np.uint16).tofile(b)  # drop 100 samples

    with pytest.raises(VerificationError):
        verify_roundtrip(b, z, num_channels=8, sampling_frequency=30000)


def test_compress_removes_stale_zarr(tmp_path):
    b = tmp_path / "Dev_ProbeA_AmplifierData_0.bin"
    _write_bin(b)
    z = tmp_path / "Dev_ProbeA_AmplifierData_0.zarr"
    z.mkdir()
    (z / "junk.txt").write_text("stale partial write from a killed run")

    res = compress_to_zarr(b, z, num_channels=8, sampling_frequency=30000)
    assert not (z / "junk.txt").exists()
    assert res.num_samples == 2000

    v = verify_roundtrip(b, z, num_channels=8, sampling_frequency=30000)
    assert v.checksum_match is True
