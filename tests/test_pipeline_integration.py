"""HPC integration tests for the DataJoint layer (run against ``aeondj``).

All tests here are marked ``integration`` and are **skipped in the default local
run** (`pytest -m "not integration"`). Run them on the SWC HPC with `aeondj`
configured (datajoint.json + .secrets), against a throwaway prefix:

    module load uv
    AEON_TEST_PREFIX=test_rawcomp uv run pytest -m integration

The schema (`<prefix>_aeon_raw_compression`) is created at module start and
dropped at the end. Synthetic data is written to pytest's ``tmp_path`` (a
writable scratch dir), so most tests need no real Ceph data or write access to
the raw store. The final test drives the WHOLE pipeline on a *copy* of one real
chunk and runs only when ``AEON_REAL_CHUNK`` points at a real
``*_AmplifierData_*.bin`` (compute node, roomy ``--basetemp``).

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


def test_deletion_deletes_original_and_is_idempotent(activated_schema, tmp_path, monkeypatch):
    # The only data-destroying code in the system. v1 keeps it source-gated
    # (DELETION_ENABLED=False), so exercise the real logic by flipping the flag
    # here -- proving it BEFORE anyone enables it against real data.
    import datetime
    from pathlib import Path

    experiment_dir = make_epoch(
        tmp_path, "AEONX1/intexp_delok", "2026-05-11T14-00-00", "NeuropixelsV2",
        ["ProbeA"], n_chunks=3, finished=True, n_channels=8,
    )
    placed_by = "delok_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 6, 26, 7, 0, 0))
    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by})
    pipeline.CompressedFile.populate({"placed_by": placed_by})

    key = (pipeline.CompressedFile & {"placed_by": placed_by}).keys()[0]
    bin_path = Path((pipeline.RawEphysDiscovery.RawEphysFile & key).fetch1("file_path"))
    assert bin_path.exists()

    monkeypatch.setattr(pipeline, "DELETION_ENABLED", True)

    # Drive it through the real populate() path (a direct make() call is blocked
    # by DataJoint's auto-populated-table insert guard). Restrict to this key.
    # 1) Original present -> deleted, recorded as original_existed=True.
    pipeline.OriginalDeletion.populate(key, suppress_errors=False)
    assert not bin_path.exists()
    assert bool((pipeline.OriginalDeletion & key).fetch1("original_existed")) is True

    # 2) Idempotent re-run: original already gone -> original_existed=False, no
    #    error. (Drop the tracking row so the key is unpopulated and re-runs.)
    (pipeline.OriginalDeletion & key).delete_quick()
    pipeline.OriginalDeletion.populate(key, suppress_errors=False)
    assert bool((pipeline.OriginalDeletion & key).fetch1("original_existed")) is False


@pytest.mark.skipif(
    not os.environ.get("AEON_REAL_CHUNK"),
    reason="set AEON_REAL_CHUNK to a real *_AmplifierData_*.bin (enabled probe) to run this",
)
def test_real_chunk_full_pipeline_roundtrip_and_deletion(
    activated_schema, tmp_path, monkeypatch
):
    """The holistic real-data test: the WHOLE pipeline, on a copy of one real chunk.

    This is the single artifact behind "if the tests pass, it works for everyone".
    On a *copy* of one real chunk it drives every step through the DataJoint
    tables and asserts each:

    * discovery parses a **real** ``Metadata.yml`` (num_channels is derived from
      it, not passed in) and registers the real chunk;
    * ``CompressedFile.make`` compresses to zarr and verifies a **byte-exact**
      round-trip on real 384-ch data (``checksum_match``);
    * the durable ``content_hash`` recipe holds on real data -- the zarr
      re-decodes to the original bytes without the original present;
    * the real ``OriginalDeletion`` path removes the original (run on the COPY,
      so it is safe; v1 still keeps deletion source-gated).

    Point ``AEON_REAL_CHUNK`` at an **enabled** probe's ``*_AmplifierData_*.bin``.
    Two things are deliberately OUT OF SCOPE (deferred to the team): read-only
    Ceph write/delete (we copy into a writable tmp dir) and the "finished
    recording" completeness detection (a 0-byte successor stub closes the copied
    chunk via the successor rule, so quiescence/epoch-finished never runs here).

    Run on a compute node, >=4h walltime, with a roomy tmp dir
    (``--basetemp=<scratch>`` -- the copy + zarr need ~3x the chunk size). ~30-50 min.
    """
    import datetime
    import hashlib
    import shutil
    from pathlib import Path

    import spikeinterface as si

    from aeon_raw_compression.discovery import _AMPLIFIER_RE

    src_chunk = Path(os.environ["AEON_REAL_CHUNK"])
    src_device_dir = src_chunk.parent
    src_epoch_dir = src_device_dir.parent
    src_metadata = src_epoch_dir / "Metadata.yml"
    assert src_metadata.exists(), f"no Metadata.yml beside {src_epoch_dir}"

    match = _AMPLIFIER_RE.search(src_chunk.name)
    assert match, f"AEON_REAL_CHUNK name not a *_AmplifierData_N.bin: {src_chunk.name!r}"
    chunk_n = int(match.group(2))

    # Writable copy: AEONX1/realcopy/<epoch>/<device>/{Metadata.yml, chunk_N, stub_{N+1}}.
    experiment_dir = tmp_path / "AEONX1" / "realcopy"
    dst_epoch = experiment_dir / src_epoch_dir.name
    dst_device = dst_epoch / src_device_dir.name
    dst_device.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_metadata, dst_epoch / "Metadata.yml")
    dst_chunk = dst_device / src_chunk.name
    shutil.copy2(src_chunk, dst_chunk)
    # 0-byte successor stub: closes the real chunk via the successor rule so this
    # test does not depend on the (deferred) quiescence / epoch-finished logic.
    stub = src_chunk.name.replace(
        f"_AmplifierData_{chunk_n}.bin", f"_AmplifierData_{chunk_n + 1}.bin"
    )
    (dst_device / stub).write_bytes(b"")

    placed_by = "real_user"
    _place_trigger(experiment_dir, placed_by, datetime.datetime(2026, 7, 1, 0, 0, 0))
    pipeline.RawEphysDiscovery.populate({"placed_by": placed_by}, suppress_errors=False)

    # Exactly the one real chunk registers; the stub is the held-back final chunk.
    registered = (
        pipeline.RawEphysDiscovery.RawEphysFile & {"placed_by": placed_by}
    ).to_dicts()
    assert [r["file_name"] for r in registered] == [src_chunk.name]
    # Independent check that the channel count parsed from the real Metadata.yml
    # fits the real file (a wrong-but-consistent count would still round-trip, so
    # checksum_match alone would not catch a metadata-parse regression).
    n_channels = registered[0]["num_channels"]
    assert n_channels > 0 and src_chunk.stat().st_size % (n_channels * 2) == 0

    pipeline.CompressedFile.populate({"placed_by": placed_by}, suppress_errors=False)
    row = (pipeline.CompressedFile & {"placed_by": placed_by}).fetch1()
    assert bool(row["checksum_match"]) is True  # byte-exact round-trip on real data
    assert row["codec_name"] == "blosc-zstd-5-bitshuffle"
    assert row["compression_ratio"] > 1.0
    assert row["zarr_path"].endswith(".zarr")
    assert len(row["content_hash"]) == 64

    # content_hash recipe on REAL data: the zarr re-decodes to the original bytes.
    zarr_traces = si.load(row["zarr_path"]).get_traces().tobytes()
    assert hashlib.sha256(zarr_traces).hexdigest() == row["content_hash"]

    # Real deletion path, exercised on the COPY (safe). Flip the source gate here
    # to prove the only data-destroying code BEFORE anyone enables it for real.
    key = (pipeline.CompressedFile & {"placed_by": placed_by}).keys()[0]
    assert dst_chunk.exists()
    monkeypatch.setattr(pipeline, "DELETION_ENABLED", True)
    pipeline.OriginalDeletion.populate(key, suppress_errors=False)
    assert not dst_chunk.exists()
    assert bool((pipeline.OriginalDeletion & key).fetch1("original_existed")) is True
