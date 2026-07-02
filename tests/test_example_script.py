"""Smoke test: the example notebook script runs end-to-end (headless).

Runs ``docs/examples/read_compressed_zarr.py`` against a small synthetic zarr so
both the SpikeInterface and the plain-zarr/numpy/matplotlib paths are exercised
without a display or real data. Not marked ``integration`` -- no DB needed.
"""

import runpy
from pathlib import Path

import matplotlib
import numpy as np

from aeon_raw_compression.compression import compress_to_zarr

matplotlib.use("Agg")

_SCRIPT = Path(__file__).parent.parent / "docs" / "examples" / "read_compressed_zarr.py"


def test_example_runs_against_synthetic_zarr(tmp_path, monkeypatch):
    bin_path = tmp_path / "Dev_ProbeA_AmplifierData_0.bin"
    np.arange(2000 * 8, dtype=np.uint16).reshape(2000, 8).tofile(bin_path)
    zarr_path = tmp_path / "Dev_ProbeA_AmplifierData_0.zarr"
    compress_to_zarr(bin_path, zarr_path, num_channels=8, sampling_frequency=30000)

    monkeypatch.setenv("AEON_EXAMPLE_ZARR", str(zarr_path))
    runpy.run_path(str(_SCRIPT))  # must run top-to-bottom without raising
