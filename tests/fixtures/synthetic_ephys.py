"""Synthetic ephys data factory for local unit tests.

Adapted from aeon_mecha's test fixtures
(``tests/fixtures/ephys/ephys_factories.py`` in the ephys-test worktrees).
Writes the minimal on-disk layout the compression library reads -- an epoch
directory with ``Metadata.yml`` and per-device ``*_AmplifierData_N.bin`` /
``*_Clock_N.bin`` chunk families -- entirely in a temp dir. No real Ceph data
is ever needed locally; the compression/verify path is format-agnostic.

Real ``Metadata.yml`` does not carry a channel-count field, so by default the
NP2 constant (384) applies. Tests that need a small, fast recording pass an
explicit ``num_channels`` here, which the parser honors (see
``aeon_raw_compression.metadata``).
"""

import json
from pathlib import Path

import numpy as np

DEFAULT_METADATA_KEY = "NeuropixelsV2e"


def write_metadata(
    epoch_dir,
    *,
    metadata_key=DEFAULT_METADATA_KEY,
    device_name="NeuropixelsV2",
    config="ConfigurationA",
    num_channels=None,
    sampling_frequency=None,
):
    """Write a minimal ``Metadata.yml`` (JSON) into ``epoch_dir``.

    Mirrors the real layout: ``Devices -> <metadata_key> -> ConfigurationA/B``
    with a ``ProbeInterfaceFileName`` (as real files have). ``num_channels`` /
    ``sampling_frequency`` are written only when given -- omit them to model the
    real-data case where the parser falls back to NP2 constants.
    """
    config_block = {"ProbeInterfaceFileName": "test-config-0.json"}
    if num_channels is not None:
        config_block["NumberOfChannels"] = num_channels
    if sampling_frequency is not None:
        config_block["SamplingFrequency"] = sampling_frequency

    meta = {"Devices": {metadata_key: {"DeviceName": device_name, config: config_block}}}

    epoch_dir = Path(epoch_dir)
    epoch_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = epoch_dir / "Metadata.yml"
    metadata_path.write_text(json.dumps(meta))
    return metadata_path


def make_amplifier_chunks(
    device_dir,
    device_name,
    probe_label,
    n_chunks,
    *,
    n_samples=200,
    n_channels=8,
    dtype="uint16",
    seed=0,
):
    """Write ``_0 .. _{n_chunks-1}`` AmplifierData/Clock chunk pairs.

    Data is a per-sample ramp plus small seeded noise so it is *compressible*
    (ratio > 1) but non-trivial -- an all-zeros buffer would make the
    compression-ratio assertions meaningless. A companion ``Clock_N.bin``
    (uint64) is written alongside each chunk to mirror the real layout (and to
    confirm discovery's glob ignores non-AmplifierData files).
    """
    device_dir = Path(device_dir)
    device_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    paths = []
    for n in range(n_chunks):
        ramp = (np.arange(n_samples, dtype=np.int64)[:, None] % 4000)
        noise = rng.integers(-3, 4, size=(n_samples, n_channels))
        data = ((ramp + noise) % 4000).astype(dtype)
        amp_path = device_dir / f"{device_name}_{probe_label}_AmplifierData_{n}.bin"
        data.tofile(amp_path)

        clock_path = device_dir / f"{device_name}_{probe_label}_Clock_{n}.bin"
        np.arange(n_samples, dtype=np.uint64).tofile(clock_path)

        paths.append(amp_path)
    return paths


def make_epoch(
    root,
    experiment,
    epoch,
    device_name,
    probes,
    n_chunks,
    *,
    finished=True,
    n_samples=200,
    n_channels=8,
):
    """Build a full synthetic epoch: ``root/experiment/epoch/{Metadata.yml,device/chunks}``.

    Writes ``Metadata.yml`` (with an explicit ``num_channels`` so the small
    synthetic recordings round-trip) and AmplifierData/Clock chunks for each
    probe. When ``finished=True`` a newer sibling epoch directory is created so
    the *default* epoch-finished detector (newer-epoch-dir check) also returns
    True; tests that inject ``is_epoch_finished`` directly do not depend on this.

    Returns the experiment directory (the path to pass to ``discover_raw_files``).
    """
    experiment_dir = Path(root) / experiment
    epoch_dir = experiment_dir / epoch
    write_metadata(epoch_dir, device_name=device_name, num_channels=n_channels)

    device_dir = epoch_dir / device_name
    for probe in probes:
        make_amplifier_chunks(
            device_dir,
            device_name,
            probe,
            n_chunks,
            n_samples=n_samples,
            n_channels=n_channels,
        )

    if finished:
        (experiment_dir / "2099-01-01T00-00-00").mkdir(parents=True, exist_ok=True)

    return experiment_dir
