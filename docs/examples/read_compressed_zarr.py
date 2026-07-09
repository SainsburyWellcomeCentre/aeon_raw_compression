# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Reading & inspecting a compressed raw-ephys zarr
#
# `aeon_raw_compression` stores each raw `*_AmplifierData_*.bin` chunk as a
# `.zarr` directory (lossless, byte-exact). This notebook shows two ways to read
# one back:
#
# 1. **With SpikeInterface** — the normal analysis path.
# 2. **Without SpikeInterface** — plain `zarr` + `numpy` + `matplotlib`, i.e. the
#    same "load an array and eyeball it" workflow you'd use on the raw `.bin`.
#
# Convert this file to a notebook with: `jupytext --to notebook read_compressed_zarr.py`

# %%
import os
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe; a live Jupyter kernel will use its own backend
import matplotlib.pyplot as plt
import numpy as np

# %% [markdown]
# ## Locate a zarr
#
# Edit `ZARR_PATH` below to point at a compressed chunk. A real zarr lives under
# the **processed** root at the same relative path as the raw `.bin`, with a
# `.zarr` extension — e.g. `.../processed/AEONX1/<exp>/<epoch>/<device>/<name>.zarr`.
# If the path doesn't exist we build a tiny synthetic zarr so the notebook still
# runs anywhere.

# %%
# --- edit this one line ---
ZARR_PATH = "/ceph/aeon/aeon/data/processed/AEONX1/<exp>/<epoch>/<device>/<name>.zarr"
# --------------------------
zarr_path = os.environ.get("AEON_EXAMPLE_ZARR", ZARR_PATH)
if not Path(zarr_path).exists():
    # Fall back to a tiny synthetic zarr so the notebook runs with no real data.
    from aeon_raw_compression.compression import compress_to_zarr

    tmp = Path(tempfile.mkdtemp())
    bin_path = tmp / "Dev_ProbeA_AmplifierData_0.bin"
    np.arange(2000 * 8, dtype=np.uint16).reshape(2000, 8).tofile(bin_path)
    zarr_path = str(tmp / "Dev_ProbeA_AmplifierData_0.zarr")
    compress_to_zarr(bin_path, zarr_path, num_channels=8, sampling_frequency=30000)
print("zarr:", zarr_path)

# %% [markdown]
# ## Option A — with SpikeInterface (recommended)
#
# `si.load` returns a recording extractor; `get_traces` reads a `(samples,
# channels)` block. Pass `start_frame`/`end_frame` so you never load the whole
# multi-GB recording at once.

# %%
import spikeinterface as si

recording = si.load(zarr_path)
print("channels:", recording.get_num_channels(), " samples:", recording.get_num_samples())
traces = recording.get_traces(start_frame=0, end_frame=1000)
print("SI traces block:", traces.shape, traces.dtype)

# %% [markdown]
# ## Option B — without SpikeInterface (plain zarr + numpy)
#
# The traces live in the `traces_seg0` array, shape `(samples, channels)`, same
# dtype as the original `.bin`. Slicing it gives a NumPy array — exactly like
# `np.fromfile(...).reshape(-1, n_channels)` on the raw file, so any
# matplotlib-based sanity check you did on the `.bin` works unchanged.

# %%
import zarr

root = zarr.open(zarr_path, mode="r")
print("arrays:", list(root.array_keys()))
block = root["traces_seg0"][0:1000]  # NumPy array (samples, channels)
print("numpy block:", block.shape, block.dtype)

# %% [markdown]
# ## Quick sanity check
#
# A few summary stats make correctness legible without eyeballing the plot: a
# real recording spans a range of values, so a block that is empty or all one
# value would signal something went wrong.

# %%
print("block:", block.shape, block.dtype)
print("min/max/mean:", block.min(), block.max(), float(block.mean()))
assert block.size and block.min() != block.max()  # not degenerate / all-constant

# %%
fig, ax = plt.subplots()
ax.plot(block[:, 0])
ax.set(xlabel="sample", ylabel="channel 0 (a.u.)", title="First 1000 samples, channel 0")
out_png = Path(tempfile.gettempdir()) / "aeon_example_channel0.png"
fig.savefig(out_png)
print("saved plot to", out_png)
