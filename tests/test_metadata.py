"""Unit tests for aeon_raw_compression.metadata.

Covers the two real-world cases (explicit override fields vs. NP2-constant
fallback), the filesystem->metadata device-name bridge, ConfigurationA/B
selection by probe label, and the missing-device error.
"""

import json

import pytest

from aeon_raw_compression.metadata import (
    NP2_NUM_CHANNELS,
    NP2_SAMPLING_FREQUENCY,
    ProbeParams,
    probe_enabled,
    read_probe_params,
)
from tests.fixtures.synthetic_ephys import write_metadata


def test_reads_explicit_channels_and_fs(tmp_path):
    # Synthetic Metadata.yml carrying explicit fields lets small-channel
    # recordings round-trip in the compression tests.
    write_metadata(tmp_path, num_channels=8, sampling_frequency=30000)
    params = read_probe_params(
        tmp_path / "Metadata.yml", device_name="NeuropixelsV2", probe_label="ProbeA"
    )
    assert isinstance(params, ProbeParams)
    assert params.num_channels == 8
    assert params.sampling_frequency == 30000.0


def test_falls_back_to_np2_constants_when_fields_absent(tmp_path):
    # Real Metadata.yml (per ceph-ephys-file-structure.md and aeon_mecha's
    # reader) has no channel-count/fs field -> NP2 constants apply.
    write_metadata(tmp_path)
    params = read_probe_params(
        tmp_path / "Metadata.yml", device_name="NeuropixelsV2", probe_label="ProbeA"
    )
    assert params.num_channels == NP2_NUM_CHANNELS
    assert params.sampling_frequency == NP2_SAMPLING_FREQUENCY


def test_bridges_filesystem_device_name_to_metadata_key(tmp_path):
    # Filesystem dir "NeuropixelsV2" maps to metadata key "NeuropixelsV2e".
    write_metadata(tmp_path, num_channels=8)
    params = read_probe_params(
        tmp_path / "Metadata.yml", device_name="NeuropixelsV2", probe_label="ProbeA"
    )
    assert params.num_channels == 8


def test_selects_configuration_b_for_probe_b(tmp_path):
    write_metadata(tmp_path, config="ConfigurationA", num_channels=8)
    meta_path = tmp_path / "Metadata.yml"
    meta = json.loads(meta_path.read_text())
    meta["Devices"]["NeuropixelsV2e"]["ConfigurationB"] = {"NumberOfChannels": 16}
    meta_path.write_text(json.dumps(meta))

    params = read_probe_params(
        meta_path, device_name="NeuropixelsV2", probe_label="ProbeB"
    )
    assert params.num_channels == 16


def test_missing_device_raises(tmp_path):
    (tmp_path / "Metadata.yml").write_text(json.dumps({"Devices": {}}))
    with pytest.raises(KeyError):
        read_probe_params(
            tmp_path / "Metadata.yml", device_name="NeuropixelsV2", probe_label="ProbeA"
        )


def test_reads_top_level_sample_rate_when_config_has_none(tmp_path):
    # Real Bonsai layout: no SamplingFrequency in ConfigurationA/B, but a
    # top-level "SampleRate" string. That wins over the NP2 constant.
    write_metadata(tmp_path)
    meta_path = tmp_path / "Metadata.yml"
    meta = json.loads(meta_path.read_text())
    meta["SampleRate"] = "30000"
    meta_path.write_text(json.dumps(meta))

    params = read_probe_params(
        meta_path, device_name="NeuropixelsV2", probe_label="ProbeA"
    )
    assert params.sampling_frequency == 30000.0


def test_config_sampling_frequency_overrides_top_level(tmp_path):
    write_metadata(tmp_path, sampling_frequency=25000)
    meta_path = tmp_path / "Metadata.yml"
    meta = json.loads(meta_path.read_text())
    meta["SampleRate"] = "30000"  # top-level present, but config wins
    meta_path.write_text(json.dumps(meta))

    params = read_probe_params(
        meta_path, device_name="NeuropixelsV2", probe_label="ProbeA"
    )
    assert params.sampling_frequency == 25000.0


def test_probe_enabled_reads_string_flag(tmp_path):
    meta_path = tmp_path / "Metadata.yml"
    meta_path.write_text(
        json.dumps({"Devices": {"ProbeA": "false", "ProbeB": "true"}})
    )
    assert probe_enabled(meta_path, "ProbeB") is True
    assert probe_enabled(meta_path, "ProbeA") is False
    # Absent flag -> assume enabled.
    assert probe_enabled(meta_path, "ProbeC") is True
