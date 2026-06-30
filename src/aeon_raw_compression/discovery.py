"""Discover complete raw ephys files eligible for compression.

A standalone filesystem scan (no aeon_mecha dependency): walk an experiment
(optionally one epoch) for ``*_AmplifierData*.bin`` chunk files, apply the
completeness rule, and return one :class:`RawFileRecord` per complete file.
Deduplication against already-registered files is the caller's concern -- the
DataJoint layer passes the set of known ``file_path``s.

Completeness (from the spec, grounded in the Bonsai/ONIX writer mechanics):

* **Successor rule (primary).** Chunk ``_N`` is closed once ``_{N+1}`` exists
  in the same device directory -- the writer holds only the current (highest)
  chunk open. Covers every chunk except the highest-numbered one.
* **Final (highest) chunk.** No successor exists, so register it only when the
  epoch is known finished *and* the file is quiescent (size/mtime stable).

``is_epoch_finished`` and ``is_quiescent`` are injectable so callers (and tests)
control them. The defaults are deliberately conservative; the exact quiescence
threshold and epoch-finished signal are tuned at integration/rollout time.

Path layout (mirrors aeon_mecha): ``experiment_dir / epoch_dir / device_name /
{device}_{Probe}_AmplifierData_{N}.bin``.
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from aeon_raw_compression.metadata import probe_enabled, read_probe_params

logger = logging.getLogger(__name__)

# Quiescence threshold for the final-chunk guard. Conservative -- well over one
# chunk's wall-clock duration, because the writer holds the handle open for the
# whole chunk and Ceph may not flush mtime promptly. TODO(tune-on-ceph).
DEFAULT_QUIESCENCE_THRESHOLD_S = 30 * 60

# "{device}_{Probe}_AmplifierData_{N}.bin" -> (probe_label, chunk_number)
_AMPLIFIER_RE = re.compile(r"_(Probe[A-Z])_AmplifierData_(\d+)\.bin$")

# Epoch directories are ISO timestamps, e.g. "2026-05-11T07-50-11". Used to
# decide which sibling dirs count when inferring whether an epoch is finished
# (a non-timestamp sibling like "golden_test_sorting" must not count).
_EPOCH_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")


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
    is_epoch_finished=None,
    is_quiescent=None,
    on_anomaly=None,
):
    """Scan ``experiment_dir`` for complete AmplifierData files.

    Args:
        experiment_dir: Absolute path to the experiment directory to scan.
        experiment_path: Label stored on each record (e.g. ``"AEONX1/exp"``).
            Defaults to ``experiment_dir.name``.
        epoch_path: If given, scan only this epoch subdirectory (by name).
        already_registered: Iterable of ``file_path`` strings to skip (dedup).
        is_epoch_finished: ``Callable[[Path], bool]`` over an epoch dir; default
            checks for a newer sibling epoch directory.
        is_quiescent: ``Callable[[Path], bool]`` over a file; default checks
            mtime age against :data:`DEFAULT_QUIESCENCE_THRESHOLD_S`.
        on_anomaly: ``Callable[[str], None]`` for anomaly messages (numbering
            gaps, unparseable names); default logs a warning.

    Returns:
        List of :class:`RawFileRecord` for complete, not-yet-registered files.
    """
    experiment_dir = Path(experiment_dir)
    experiment_path = experiment_path or experiment_dir.name
    is_epoch_finished = is_epoch_finished or _default_is_epoch_finished
    is_quiescent = is_quiescent or _default_is_quiescent
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
        probe_label, chunk_n = parsed[f]
        chunks = groups[(f.parent, probe_label)]

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

        file_path = f.resolve().as_posix()
        if file_path in already:
            continue

        if not _is_complete(
            f, chunk_n, chunks, experiment_dir / epoch_dir,
            is_epoch_finished, is_quiescent,
        ):
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


def _is_complete(file, chunk_n, chunks, epoch_dir_path, is_epoch_finished, is_quiescent):
    """Apply the successor rule, with the epoch-finished/quiescent final guard."""
    if (chunk_n + 1) in chunks:
        return True  # successor exists -> chunk is closed
    if chunk_n != max(chunks):
        # No direct successor but not the highest chunk: a numbering gap. Can't
        # prove closure by the successor rule -> conservatively exclude.
        return False
    # Highest-numbered chunk: only complete once the epoch is finished and the
    # file has stopped changing.
    return bool(is_epoch_finished(epoch_dir_path) and is_quiescent(file))


def _flag_numbering_gaps(groups, on_anomaly):
    for (device_dir, probe_label), chunks in groups.items():
        nums = sorted(chunks)
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        if missing:
            on_anomaly(
                f"Gap in chunk numbering for {device_dir.name}/{probe_label}: "
                f"missing {missing}"
            )


def _default_is_quiescent(file, *, threshold_s=DEFAULT_QUIESCENCE_THRESHOLD_S):
    try:
        mtime = Path(file).stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) >= threshold_s


def _default_is_epoch_finished(epoch_dir):
    """Finished if a newer sibling *epoch* directory (later ISO timestamp) exists.

    Only ISO-timestamp-looking siblings count -- otherwise an unrelated sibling
    such as ``golden_test_sorting`` (which sorts lexically after the timestamp)
    would falsely mark the epoch finished.
    """
    epoch_dir = Path(epoch_dir)
    if not _EPOCH_DIR_RE.match(epoch_dir.name):
        return False  # not a recognizable epoch dir -- don't guess
    try:
        siblings = [
            p.name
            for p in epoch_dir.parent.iterdir()
            if p.is_dir() and _EPOCH_DIR_RE.match(p.name)
        ]
    except OSError:
        return False
    return any(name > epoch_dir.name for name in siblings)
