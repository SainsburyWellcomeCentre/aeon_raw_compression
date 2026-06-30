"""Unit tests for aeon_raw_compression.discovery.

Completeness (successor rule + final-chunk epoch-finished/quiescent guard),
scoping, dedup, and probe-param population. ``is_epoch_finished`` and
``is_quiescent`` are injected so the tests are deterministic (no clock waits).
"""

import json

import pytest

from aeon_raw_compression.discovery import RawFileRecord, discover_raw_files
from tests.fixtures.synthetic_ephys import make_epoch

_ALWAYS = lambda *_: True
_NEVER = lambda *_: False


def test_unfinished_epoch_skips_final_chunk(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=False,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_NEVER, is_quiescent=_ALWAYS,
    )
    names = sorted(r.file_name for r in recs)
    assert names == [
        "NeuropixelsV2_ProbeA_AmplifierData_0.bin",
        "NeuropixelsV2_ProbeA_AmplifierData_1.bin",
    ]  # _2 is the final chunk; excluded because the epoch is not finished


def test_includes_final_chunk_when_finished_and_quiescent(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
    )
    names = sorted(r.file_name for r in recs)
    assert len(names) == 3
    assert names[-1] == "NeuropixelsV2_ProbeA_AmplifierData_2.bin"


def test_excludes_final_chunk_when_not_quiescent(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "e", "NeuropixelsV2", ["ProbeA"],
        n_chunks=2, finished=True,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_ALWAYS, is_quiescent=_NEVER,
    )
    names = sorted(r.file_name for r in recs)
    assert names == ["NeuropixelsV2_ProbeA_AmplifierData_0.bin"]  # _1 final, not quiescent


def test_dedup_against_already_registered(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "e", "NeuropixelsV2", ["ProbeA"],
        n_chunks=3, finished=True,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
    )
    seen = {recs[0].file_path}
    recs2 = discover_raw_files(
        tmp_path / "AEONX1/exp", already_registered=seen,
        is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
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
        tmp_path / "AEONX1/exp", epoch_path="epoch-1",
        is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
    )
    assert {r.epoch_dir for r in recs} == {"epoch-1"}


def test_records_carry_probe_params(tmp_path):
    make_epoch(
        tmp_path, "AEONX1/exp", "e", "NeuropixelsV2", ["ProbeA"],
        n_chunks=2, n_channels=8, finished=True,
    )
    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
    )
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

    recs = discover_raw_files(
        tmp_path / "AEONX1/exp", is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS,
    )
    assert recs == []


def test_default_epoch_finished_ignores_non_timestamp_sibling(tmp_path):
    # A non-timestamp sibling (like golden_test_sorting) must NOT mark the epoch
    # finished, so the final chunk stays excluded under the default detector.
    exp = make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=False, n_channels=8,
    )
    (exp / "golden_test_sorting").mkdir()

    recs = discover_raw_files(exp, is_quiescent=_ALWAYS)  # default is_epoch_finished
    names = sorted(r.file_name for r in recs)
    assert names == ["NeuropixelsV2_ProbeA_AmplifierData_0.bin"]  # _1 (final) excluded


def test_default_epoch_finished_accepts_newer_timestamp_sibling(tmp_path):
    exp = make_epoch(
        tmp_path, "AEONX1/exp", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=False, n_channels=8,
    )
    (exp / "2026-05-12T07-50-11").mkdir()  # a genuinely newer epoch dir

    recs = discover_raw_files(exp, is_quiescent=_ALWAYS)  # default is_epoch_finished
    names = sorted(r.file_name for r in recs)
    assert names == [
        "NeuropixelsV2_ProbeA_AmplifierData_0.bin",
        "NeuropixelsV2_ProbeA_AmplifierData_1.bin",  # final chunk now included
    ]


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

    recs = discover_raw_files(sandbox, is_epoch_finished=_ALWAYS, is_quiescent=_ALWAYS)
    assert recs
    for r in recs:
        assert "sandbox" in r.file_path  # kept the symlink path
        assert "/real/" not in r.file_path  # NOT resolved to the real target
