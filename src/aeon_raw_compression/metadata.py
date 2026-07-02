"""Parse ``Metadata.yml`` for the parameters needed to read an AmplifierData binary.

Reimplements aeon_mecha's known parsing rules **without importing aeon_mecha**
(decoupling is a team-accepted cost; the parser may drift if aeon_mecha's
handling changes).

Channel count and sampling frequency
------------------------------------
Neuropixels 2.0 hardware is fixed at 384 channels @ 30 kHz, ``uint16``.
aeon_mecha treats these as constants: it derives ``num_channels`` from the
electrode configuration in the database (=384) and hardcodes the sampling rate
(``spike_sorting.py``: ``fs_hz = 30e3  # TODO: read this from the metadata``).

Real ``Metadata.yml`` does **not** carry a clean channel-count field -- the
channel map lives in a separate probeinterface JSON
(``M81_ProbeB_*.json``), and ``ConfigurationA/B`` holds a
``ProbeInterfaceFileName`` rather than a count. So the NP2 constants below are
the reliable source. If a config block *does* carry an explicit field, it wins
(synthetic test fixtures use this so small recordings round-trip).

TODO(confirm-on-ceph): the exact field name (if any) for channel count in real
Metadata.yml, and the V2Beta active-config key. Covered by the Task 8
integration test against real data.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# Neuropixels 2.0 probe-type constants (see module docstring).
NP2_NUM_CHANNELS = 384
NP2_SAMPLING_FREQUENCY = 30000.0
NP2_DTYPE = "uint16"

# Filesystem device-dir name -> Metadata.yml "Devices" key candidates, in
# preference order. The filesystem uses "NeuropixelsV2"; metadata uses
# "NeuropixelsV2e" (see knowledge/ceph-ephys-file-structure.md). The V2Beta
# active-config key is unconfirmed -- try the Beta-specific key first, then the
# V2 key. TODO(confirm-on-ceph).
_DEVICE_KEY_CANDIDATES = {
    "NeuropixelsV2": ["NeuropixelsV2e"],
    "NeuropixelsV2Beta": ["NeuropixelsV2eBeta", "NeuropixelsV2e"],
}

# Optional override fields inside ConfigurationA/B. Real data typically omits
# these (NP2 constants apply); synthetic fixtures set them.
_NUM_CHANNELS_FIELD = "NumberOfChannels"
_SAMPLING_FREQUENCY_FIELD = "SamplingFrequency"

# Real Bonsai Metadata.yml stores the sampling rate at the TOP LEVEL as
# "SampleRate" (a string, e.g. "30000"), not inside ConfigurationA/B. Confirmed
# on the abcGolden01 golden data (2026-06-29).
_TOP_LEVEL_SAMPLE_RATE_FIELD = "SampleRate"


@dataclass(frozen=True)
class ProbeParams:
    """Parameters for ``spikeinterface.extractors.read_binary`` of one probe."""

    num_channels: int
    sampling_frequency: float


def read_probe_params(metadata_path, device_name: str, probe_label: str) -> ProbeParams:
    """Read a probe's ``read_binary`` parameters from ``Metadata.yml``.

    ``Metadata.yml`` is JSON despite the extension, so it is parsed with the
    stdlib ``json`` module.

    Args:
        metadata_path: Path to the epoch's ``Metadata.yml``.
        device_name: Filesystem device-dir name, e.g. ``"NeuropixelsV2"``.
        probe_label: Probe label from the filename, e.g. ``"ProbeA"`` /
            ``"ProbeB"`` (selects ConfigurationA/B).

    Returns:
        ProbeParams(num_channels, sampling_frequency).

    Raises:
        KeyError: if the device's config (or its ConfigurationA/B) is absent --
            e.g. a non-ephys epoch, which must not be registered for compression.
    """
    meta = json.loads(Path(metadata_path).read_text())
    devices = meta.get("Devices", {})
    device_key = _resolve_device_key(devices, device_name)
    config = _select_config(devices[device_key], probe_label)

    num_channels = int(config.get(_NUM_CHANNELS_FIELD, NP2_NUM_CHANNELS))
    sampling_frequency = _resolve_sampling_frequency(meta, config)
    return ProbeParams(num_channels=num_channels, sampling_frequency=sampling_frequency)


def _resolve_sampling_frequency(meta: dict, config: dict) -> float:
    """Sampling rate: ConfigurationA/B override -> top-level SampleRate -> NP2 const."""
    raw = config.get(_SAMPLING_FREQUENCY_FIELD) or meta.get(_TOP_LEVEL_SAMPLE_RATE_FIELD)
    return float(raw) if raw is not None else NP2_SAMPLING_FREQUENCY


def probe_enabled(metadata_path, probe_label: str) -> bool:
    """Whether a probe is enabled in Metadata.yml.

    Bonsai records per-probe enable flags at ``Devices[<probe_label>]`` as the
    STRING ``"true"``/``"false"`` (e.g. ProbeA is a disabled SpoofProbe on the
    golden data). Absent flag -> assume enabled.
    """
    meta = json.loads(Path(metadata_path).read_text())
    flag = meta.get("Devices", {}).get(probe_label)
    if flag is None:
        return True
    return str(flag).strip().lower() != "false"


def _resolve_device_key(devices: dict, device_name: str) -> str:
    """Map a filesystem device-dir name to its Metadata.yml ``Devices`` key."""
    for candidate in _DEVICE_KEY_CANDIDATES.get(device_name, []):
        if candidate in devices:
            return candidate
    if device_name in devices:  # accept the filesystem name itself as a fallback
        return device_name
    raise KeyError(
        f"No metadata config for device {device_name!r}; Devices keys present: {sorted(devices)}"
    )


def _select_config(device_meta: dict, probe_label: str) -> dict:
    """Pick ConfigurationA/B for a probe label (``...B`` -> ConfigurationB)."""
    key = "ConfigurationB" if probe_label.endswith("B") else "ConfigurationA"
    if key not in device_meta:
        raise KeyError(f"{key!r} not in device metadata (keys present: {sorted(device_meta)})")
    return device_meta[key]
