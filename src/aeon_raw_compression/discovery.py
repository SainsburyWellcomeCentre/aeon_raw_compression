"""Discover complete raw ephys files eligible for compression.

A standalone filesystem scan (no aeon_mecha dependency): walk an experiment
(optionally one epoch) for ``*_AmplifierData*.bin`` chunk files, apply the
completeness rule, and return one :class:`RawFileRecord` per complete file.
Deduplication against already-registered files is the caller's concern -- the
DataJoint layer passes the set of known ``file_path``s.

Completeness: a file is eligible once it has been left untouched long enough --
its mtime is at least ``min_age_s`` seconds in the past (default 1 h). This is a
deliberately simple guard against compressing a file that is still being written
to Ceph: the recording computer's RoboCopy sometimes delivers a file mid-upload,
and a file that hasn't changed in an hour is safely complete. ``is_complete`` is
injectable so tests can control eligibility without waiting on a real clock.

Path layout (mirrors aeon_mecha): ``experiment_dir / epoch_dir / device_name /
{device}_{Probe}_AmplifierData_{N}.bin``.
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from aeon_raw_compression.metadata import probe_enabled, read_probe_params

logger = logging.getLogger(__name__)

# A file is eligible for compression once its mtime is at least this many
# seconds in the past -- i.e. it has been left untouched long enough that it is
# not still being written/uploaded. Env-overridable (tune on Ceph).
DEFAULT_MIN_AGE_S = int(os.environ.get("AEON_RAW_COMPRESSION_MIN_AGE_S", 60 * 60))

# "{device}_{Probe}_AmplifierData_{N}.bin" -> (probe_label, chunk_number)
_AMPLIFIER_RE = re.compile(r"_(Probe[A-Z])_AmplifierData_(\d+)\.bin$")


@dataclass(frozen=True)
class RawFileRecord:
    """One complete raw file eligible for compression."""

    experiment_path: str
    epoch_dir: str
    device_name: str
    probe_label: str
    file_name: str
    file_path: str  # absolute path (POSIX form), the registry key
    file_size_bytes: int
    num_channels: int
    sampling_frequency: float


def discover_raw_files(
    experiment_dir,
    *,
    experiment_path=None,
    epoch_path=None,
    already_registered=(),
    min_age_s=DEFAULT_MIN_AGE_S,
    is_complete=None,
    on_anomaly=None,
):
    """Scan ``experiment_dir`` for complete AmplifierData files.

    Args:
        experiment_dir: Absolute path to the experiment directory to scan.
        experiment_path: Label stored on each record (e.g. ``"AEONX1/exp"``).
            Defaults to ``experiment_dir.name``.
        epoch_path: If given, scan only this epoch subdirectory (by name).
        already_registered: Iterable of ``file_path`` strings to skip (dedup).
        min_age_s: A file is eligible only once its mtime is at least this many
            seconds in the past (default :data:`DEFAULT_MIN_AGE_S`). Ignored when
            ``is_complete`` is supplied.
        is_complete: ``Callable[[Path], bool]`` deciding eligibility; default is
            the ``min_age_s`` mtime-age check. Injectable for deterministic tests.
        on_anomaly: ``Callable[[str], None]`` for anomaly messages (numbering
            gaps, unparseable names); default logs a warning.

    Returns:
        List of :class:`RawFileRecord` for complete, not-yet-registered files.
    """
    experiment_dir = Path(experiment_dir)
    experiment_path = experiment_path or experiment_dir.name
    is_complete = is_complete or (
        lambda f: (time.time() - Path(f).stat().st_mtime) >= min_age_s
    )
    on_anomaly = on_anomaly or logger.warning
    already = set(already_registered)

    scan_root = experiment_dir / epoch_path if epoch_path else experiment_dir
    if not scan_root.exists():
        return []

    amp_files = sorted(
        scan_root.rglob("*_AmplifierData*.bin"), key=lambda p: p.as_posix()
    )

    # Group by (device directory, probe label) -> {chunk_number: path}. The
    # successor rule applies within a single probe's chunk family.
    groups: dict[tuple[Path, str], dict[int, Path]] = {}
    parsed: dict[Path, tuple[str, int]] = {}
    for f in amp_files:
        match = _AMPLIFIER_RE.search(f.name)
        if not match:
            on_anomaly(f"Cannot parse probe/chunk from {f.name!r}; skipping")
            continue
        probe_label, chunk_n = match.group(1), int(match.group(2))
        parsed[f] = (probe_label, chunk_n)
        groups.setdefault((f.parent, probe_label), {})[chunk_n] = f

    _flag_numbering_gaps(groups, on_anomaly)

    records: list[RawFileRecord] = []
    params_cache: dict[tuple[str, str, str], object] = {}
    enabled_cache: dict[tuple[str, str], bool] = {}
    for f in amp_files:
        if f not in parsed:
            continue
        probe_label = parsed[f][0]

        rel_parts = f.relative_to(experiment_dir).parts
        if len(rel_parts) < 3:
            on_anomaly(f"Unexpected path structure for {f}; skipping")
            continue
        epoch_dir = rel_parts[0]
        device_name = rel_parts[-2]
        metadata_path = experiment_dir / epoch_dir / "Metadata.yml"

        # Skip probes flagged disabled in Metadata.yml (e.g. ProbeA SpoofProbe).
        enabled_key = (epoch_dir, probe_label)
        if enabled_key not in enabled_cache:
            enabled_cache[enabled_key] = probe_enabled(metadata_path, probe_label)
        if not enabled_cache[enabled_key]:
            on_anomaly(f"Skipping {f.name}: probe {probe_label} is disabled in Metadata.yml")
            continue

        # Keep the path AS WALKED -- do NOT resolve() symlinks. In a symlinked
        # sandbox (golden data: ~/sciops-data symlinks -> read-only Ceph) the
        # zarr is written beside this path, so it must stay in the writable
        # location. In production (no symlinks) resolve() is a no-op anyway.
        file_path = f.as_posix()
        if file_path in already:
            continue

        if not is_complete(f):
            continue

        cache_key = (epoch_dir, device_name, probe_label)
        if cache_key not in params_cache:
            params_cache[cache_key] = read_probe_params(
                metadata_path, device_name, probe_label
            )
        params = params_cache[cache_key]

        records.append(
            RawFileRecord(
                experiment_path=experiment_path,
                epoch_dir=epoch_dir,
                device_name=device_name,
                probe_label=probe_label,
                file_name=f.name,
                file_path=file_path,
                file_size_bytes=f.stat().st_size,
                num_channels=params.num_channels,
                sampling_frequency=params.sampling_frequency,
            )
        )
    return records


def _flag_numbering_gaps(groups, on_anomaly):
    for (device_dir, probe_label), chunks in groups.items():
        nums = sorted(chunks)
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        if missing:
            on_anomaly(
                f"Gap in chunk numbering for {device_dir.name}/{probe_label}: "
                f"missing {missing}"
            )
