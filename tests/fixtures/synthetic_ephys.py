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
