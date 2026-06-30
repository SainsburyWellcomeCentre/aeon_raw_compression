"""HPC integration tests for the DataJoint layer (run against ``aeondj``).

All tests here are marked ``integration`` and are **skipped in the default local
run** (`pytest -m "not integration"`). Run them on the SWC HPC with `aeondj`
configured (datajoint.json + .secrets), against a throwaway prefix:

    module load uv
    AEON_TEST_PREFIX=test_rawcomp uv run pytest -m integration

The schema (`<prefix>_aeon_raw_compression`) is created at module start and
dropped at the end. Synthetic data is written to pytest's ``tmp_path`` (a
writable scratch dir), so no real Ceph data or write access to the raw store is
needed for tests 1-4. Test 5 exercises a real golden chunk only when
``AEON_GOLDEN_CHUNK`` is set.

Determinism note: ``RawEphysDiscovery.make`` uses discovery's *default*
completeness rule. Freshly-written synthetic files are not yet "quiescent" (the
threshold is ~30 min), so the **final** chunk of each probe family is excluded
and only the successor-covered chunks register. The assertions below rely on
exactly that behavior -- e.g. 3 chunks -> 2 registered (``_0``, ``_1``).
"""

import os

import pytest

from aeon_raw_compression import pipeline
from tests.fixtures.synthetic_ephys import make_epoch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def activated_schema():
    """Activate the compression schema on a throwaway prefix; drop it after."""
    prefix = os.environ.get("AEON_TEST_PREFIX", "test_rawcomp")
    pipeline.activate(prefix)
    yield pipeline
    pipeline.schema.drop(prompt=False)


def _place_trigger(experiment_dir, placed_by, trigger_time):
    pipeline.RawEphysDiscoveryTrigger.insert1(
        {
            "trigger_time": trigger_time,
            "placed_by": placed_by,
            "experiment_path": str(experiment_dir),  # absolute -> used as-is
            "epoch_path": "",
        }
    )


def test_discovery_registers_complete_files(activated_schema, tmp_path):
    import datetime

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_disc", "2026-05-11T07-50-11", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    placed_by = "disc_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 1, 0, 0))

    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})

    files = pipeline.RawEphysDiscovery.RawEphysFile & {"placed_by": placed_by}
    names = sorted(files.to_arrays("file_name"))
    # _2 is the final chunk; fresh files are not quiescent yet -> excluded.
    assert names == [
        "NeuropixelsV2_ProbeA_AmplifierData_0.bin",
        "NeuropixelsV2_ProbeA_AmplifierData_1.bin",
    ]
    assert (
        pipeline.RawEphysDiscovery & {"placed_by": placed_by}
    ).fetch1("num_files_found") == 2


def test_compression_produces_verified_rows(activated_schema, tmp_path):
    import datetime

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_comp", "2026-05-11T08-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    placed_by = "comp_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 2, 0, 0))

    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})
    pipeline.CompressedFile.populate({"placed_by": placed_by})

    rows = (pipeline.CompressedFile & {"placed_by": placed_by}).to_dicts()
    assert len(rows) == 2  # the two complete (non-final) chunks
    for row in rows:
        assert bool(row["checksum_match"]) is True
        assert row["codec_name"] == "blosc-zstd-5-bitshuffle"
        assert row["compression_ratio"] > 1.0
        assert row["num_samples"] == 200
        assert row["zarr_path"].endswith(".zarr")


def test_placed_by_restriction_registers_only_that_users_files(activated_schema, tmp_path):
    import datetime

    alice_dir = make_epoch(
        tmp_path, "AEONX1/intexp_alice", "2026-05-11T09-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=True, n_channels=8,
    )
    bob_dir = make_epoch(
        tmp_path, "AEONX1/intexp_bob", "2026-05-11T10-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=True, n_channels=8,
    )
    _place_trigger(alice_dir, "alice", datetime.datetime(2026, 6, 26, 3, 0, 0))
    _place_trigger(bob_dir, "bob", datetime.datetime(2026, 6, 26, 3, 0, 0))

    # Restrict the populate to alice's triggers only.
    pipeline.RawEphysDiscovery.populate({"placed_by": "alice"})

    assert len(pipeline.RawEphysDiscovery & {"placed_by": "alice"}) == 1
    assert len(pipeline.RawEphysDiscovery & {"placed_by": "bob"}) == 0
    assert len(pipeline.RawEphysDiscovery.RawEphysFile & {"placed_by": "bob"}) == 0


def test_overlapping_triggers_do_not_double_register(activated_schema, tmp_path):
    import datetime

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_overlap", "2026-05-11T12-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    _place_trigger(experiment_dir, "ov_user", datetime.datetime(2026, 6, 26, 5, 0, 0))
    _place_trigger(experiment_dir, "ov_user", datetime.datetime(2026, 6, 26, 5, 0, 1))

    # Both triggers cover the same dir; the second must not raise on the unique
    # index and must not create duplicate registry rows.
    pipeline.RawEphysDiscovery.populate({"placed_by": "ov_user"}, suppress_errors=False)

    files = pipeline.RawEphysDiscovery.RawEphysFile & {"placed_by": "ov_user"}
    paths = files.to_arrays("file_path")
    assert len(paths) == len(set(paths))  # no duplicate physical files


def test_discovery_persists_anomalies(activated_schema, tmp_path):
    import datetime

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_anom", "2026-05-11T12-30-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    gap = (
        experiment_dir / "2026-05-11T12-30-00" / "NeuropixelsV2"
        / "NeuropixelsV2_ProbeA_AmplifierData_1.bin"
    )
    gap.unlink()  # leaves chunks 0 and 2 -> a numbering gap at 1
    placed_by = "anom_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 5, 30, 0))

    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})

    row = (pipeline.RawEphysDiscovery & {"placed_by": placed_by}).fetch1()
    assert row["num_anomalies"] >= 1
    assert "Gap" in row["anomalies"]


def test_deletion_refuses_while_disabled(activated_schema, tmp_path):
    import datetime

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_del", "2026-05-11T11-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=2, finished=True, n_channels=8,
    )
    placed_by = "del_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 4, 0, 0))

    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})
    pipeline.CompressedFile.populate({"placed_by": placed_by})

    assert pipeline.DELETION_ENABLED is False
    key = (pipeline.CompressedFile & {"placed_by": placed_by}).keys()[0]
    with pytest.raises(RuntimeError):
        pipeline.OriginalDeletion().make(key)


def test_failed_verification_inserts_no_row_and_removes_zarr(
    activated_schema, tmp_path, monkeypatch
):
    import datetime
    from pathlib import Path

    from aeon_raw_compression import compression

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_failverify", "2026-05-11T13-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    placed_by = "failv_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 6, 0, 0))
    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})

    def _boom(*a, **k):
        raise compression.VerificationError("forced failure for test")

    # pipeline.py imports verify_roundtrip by name, so patch it on the pipeline
    # module (that is the bound reference make() calls).
    monkeypatch.setattr(pipeline, "verify_roundtrip", _boom)

    # Errors are suppressed -> populate returns, but no row should be inserted.
    pipeline.CompressedFile.populate({"placed_by": placed_by}, suppress_errors=True)

    assert len(pipeline.CompressedFile & {"placed_by": placed_by}) == 0
    # The untrusted zarr must have been removed so a retry starts clean.
    assert list(Path(experiment_dir).rglob("*.zarr")) == []


@pytest.mark.skipif(
    not os.environ.get("AEON_GOLDEN_CHUNK"),
    reason="set AEON_GOLDEN_CHUNK to a real *_AmplifierData_*.bin to run this",
)
def test_golden_chunk_roundtrip_and_stem_contract(tmp_path):
    """A real golden chunk reads back byte-exact and the zarr keeps the stem.

    The same-stem/.zarr naming is the contract the (future) aeon_mecha read-side
    resolver relies on. The resolver itself ships in a separate aeon_mecha PR, so
    the downstream-find half is left as a documented TODO here.
    """
    from pathlib import Path

    from aeon_raw_compression.compression import compress_to_zarr, verify_roundtrip

    bin_path = Path(os.environ["AEON_GOLDEN_CHUNK"])
    num_channels = int(os.environ.get("AEON_GOLDEN_NUM_CHANNELS", "384"))
    zarr_path = tmp_path / (bin_path.stem + ".zarr")

    result = compress_to_zarr(bin_path, zarr_path, num_channels, 30000)
    assert result.codec_name == "blosc-zstd-5-bitshuffle"
    assert Path(result.zarr_path).name == bin_path.stem + ".zarr"  # stem contract

    verification = verify_roundtrip(bin_path, zarr_path, num_channels, 30000)
    assert verification.checksum_match is True
    # TODO(companion-PR): assert the aeon_mecha resolver finds this .zarr by stem.
