"""Unit tests for aeon_raw_compression.discovery.

Completeness (a file is eligible once its mtime is at least ``min_age_s`` in the
past), scoping, dedup, probe-param population, and anomaly reporting. Tests that
don't care about the age gate pass ``min_age_s=0`` (every file counts as old
enough); the two age-specific tests drive the fixture's ``mtime_age_s`` instead.
"""

import json

import pytest

from aeon_raw_compression.discovery import RawFileRecord, discover_raw_files
from tests.fixtures.synthetic_ephys import make_epoch


def test_file_untouched_long_enough_is_eligible(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, n_channels=8, mtime_age_s=7200,
    )
    recs = discover_raw_files(tmp_path / "AEONX1/exp", experiment_path="AEONX1/exp")
    # Every chunk whose mtime is old enough registers -- no successor-rule
    # exclusion of the final chunk anymore.
    assert sorted(r.file_name for r in recs) == [
        "NeuropixelsV2_ProbeA_AmplifierData_0.bin",
        "NeuropixelsV2_ProbeA_AmplifierData_1.bin",
    ]


def test_recently_modified_file_is_held(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, n_channels=8, mtime_age_s=0,  # just written
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", experiment_path="AEONX1/exp", min_age_s=3600,
    )
    assert recs == []  # nothing is old enough -> possibly still uploading


def test_dedup_against_already_registered(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "e", "NeuropixelsV2", ["ProbeA"],
        n_chunks=3, finished=True,
    )
    recs = discover_raw_files(tmp_path / "AEONX1/exp", min_age_s=0)
    seen = {recs[0].file_path}
    recs2 = discover_raw_files(
        tmp_path / "AEONX1/exp", already_registered=seen, min_age_s=0,
    )
    assert recs[0].file_path not in {r.file_path for r in recs2}
    assert len(recs2) == len(recs) - 1


def test_scoping_to_epoch_path(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "epoch-1", "NeuropixelsV2", ["ProbeA"],
        n_chunks=2, finished=True,
    )
    make_epoch(
        tmp_path, "AEONX1/exp", "epoch-2", "NeuropixelsV2", ["ProbeA"],
        n_chunks=2, finished=True,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", epoch_path="epoch-1", min_age_s=0,
    )
    assert {r.epoch_dir for r in recs} == {"epoch-1"}


def test_records_carry_probe_params(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "e", "NeuropixelsV2", ["ProbeA"],
        n_chunks=2, n_channels=8, finished=True,
    )
    recs = discover_raw_files(tmp_path / "AEONX1/exp", min_age_s=0)
    assert recs and all(isinstance(r, RawFileRecord) for r in recs)
    r = recs[0]
    assert r.num_channels == 8
    assert r.sampling_frequency == 30000.0
    assert r.device_name == "NeuropixelsV2"
    assert r.probe_label == "ProbeA"
    assert r.file_size_bytes > 0
    assert r.experiment_path  # populated


def test_disabled_probe_is_skipped(tmp_path):
    # ProbeA flagged disabled in Metadata.yml (a SpoofProbe) -> never registered.
    make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    meta_path = tmp_path / "AEONX1/exp" / "2026-05-11T07-50-11" / "Metadata.yml"
    meta = json.loads(meta_path.read_text())
    meta["Devices"]["ProbeA"] = "false"
    meta_path.write_text(json.dumps(meta))

    recs = discover_raw_files(tmp_path / "AEONX1/exp", min_age_s=0)
    assert recs == []


def test_numbering_gap_is_reported(tmp_path):
    exp = make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    gap = (
        exp / "2026-05-11T07-50-11" / "NeuropixelsV2"
        / "NeuropixelsV2_ProbeA_AmplifierData_1.bin"
    )
    gap.unlink()  # leaves chunks 0 and 2 -> a gap at 1

    msgs = []
    discover_raw_files(exp, on_anomaly=msgs.append, min_age_s=0)
    assert any("Gap" in m and "missing" in m for m in msgs)


def test_unparseable_amplifier_name_is_reported(tmp_path):
    # A file matching the *_AmplifierData*.bin glob but not the probe/chunk regex
    # (here: no _ProbeX_, non-numeric index) is skipped with an anomaly, not
    # silently dropped.
    exp = make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=True, n_channels=8,
    )
    dev = exp / "2026-05-11T07-50-11" / "NeuropixelsV2"
    (dev / "NeuropixelsV2_AmplifierData_junk.bin").write_bytes(b"\x00" * 16)

    msgs = []
    discover_raw_files(exp, on_anomaly=msgs.append, min_age_s=0)
    assert any("Cannot parse" in m for m in msgs)


def test_unexpected_path_structure_is_reported(tmp_path):
    # A validly-named amp file sitting directly under the experiment dir (no
    # epoch/device subdirs) can't yield an epoch/device -> anomaly, not a record.
    exp = tmp_path / "AEONX1/exp"
    exp.mkdir(parents=True)
    (exp / "NeuropixelsV2_ProbeA_AmplifierData_0.bin").write_bytes(b"\x00" * 16)

    msgs = []
    recs = discover_raw_files(exp, on_anomaly=msgs.append, min_age_s=0)
    assert recs == []
    assert any("Unexpected path structure" in m for m in msgs)


def test_file_path_is_not_symlink_resolved(tmp_path):
    # In a symlinked sandbox (like ~/sciops-data -> Ceph), file_path must keep
    # the symlink path so the zarr writes to the writable side, not the real
    # (read-only) target. Skipped where symlinks aren't permitted (e.g. Windows).
    make_epoch(
        tmp_path / "real", "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=True, n_channels=8,
    )
    sandbox = tmp_path / "sandbox"
    try:
        sandbox.symlink_to(tmp_path / "real" / "AEONX1" / "exp", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")

    recs = discover_raw_files(sandbox, min_age_s=0)
    assert recs
    for r in recs:
        assert "sandbox" in r.file_path  # kept the symlink path
        assert "/real/" not in r.file_path  # NOT resolved to the real target
