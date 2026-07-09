"""Structure tests for the DataJoint pipeline -- no database connection.

These assert the module imports DB-free (deferred ``schema.activate``), the four
tables exist with the right DJ base classes, the part table is ``RawEphysFile``,
and deletion is hard-disabled in source. The populate logic is exercised by the
HPC integration tests (Task 8), not here.
"""

from pathlib import Path

import datajoint as dj
import pytest

from aeon_raw_compression import pipeline


def test_package_exports_import_first_api():
    # `import aeon_raw_compression as arc` must expose the full import-first API
    # (functions + the four tables) so a user can do everything without reaching
    # into submodules. Importing must still open no DB connection.
    import aeon_raw_compression as arc

    for name in (
        "activate",
        "add_trigger",
        "run",
        "report_deletable",
        "RawEphysDiscoveryTrigger",
        "RawEphysDiscovery",
        "CompressedFile",
        "RawEphysFileDeletion",
        "DELETION_ENABLED",
        "pipeline",
    ):
        assert hasattr(arc, name), name
    assert arc.pipeline.schema.is_activated() is False  # no DB connection at import


def test_run_summary_str_format():
    # RunSummary's __str__ is what the nightly CLI prints; pin the shape.
    from aeon_raw_compression.pipeline import RunSummary

    assert str(RunSummary(num_registered=5, num_compressed=3, num_errored=2)) == (
        "registered=5 compressed=3 errored=2"
    )


def test_four_tables_exist_with_correct_base_classes():
    assert issubclass(pipeline.RawEphysDiscoveryTrigger, dj.Manual)
    assert issubclass(pipeline.RawEphysDiscovery, dj.Imported)
    assert issubclass(pipeline.CompressedFile, dj.Computed)
    assert issubclass(pipeline.RawEphysFileDeletion, dj.Computed)


def test_part_table_is_named_raw_ephys_file():
    assert issubclass(pipeline.RawEphysDiscovery.RawEphysFile, dj.Part)


def test_deletion_disabled_in_source():
    assert pipeline.DELETION_ENABLED is False


def test_import_is_db_free():
    # Importing succeeded above with no datajoint.json / .secrets present, which
    # proves no connection was opened. The deferred schema must not be activated.
    assert pipeline.schema.is_activated() is False


def test_schema_name_has_single_separator():
    # Project prefixes end in "_"; the schema name must not double the underscore.
    assert (
        pipeline._schema_name("u_elissas_aeon_ephys_v2_test_")
        == "u_elissas_aeon_ephys_v2_test_aeon_raw_compression"
    )
    assert pipeline._schema_name("test_rawcomp") == "test_rawcomp_aeon_raw_compression"


def test_resolve_experiment_dir_relative_vs_absolute(tmp_path):
    # Absolute experiment_path is used as-is (the path tests pass directly).
    abs_dir = tmp_path / "AEONX1" / "exp"
    assert pipeline._resolve_experiment_dir(str(abs_dir)) == Path(str(abs_dir))

    # A RELATIVE experiment_path -- the real add_trigger.py "AEONX1/exp" case --
    # is resolved against RAW_DATA_ROOT. This production path is otherwise
    # untested (the integration tests all pass absolute dirs).
    resolved = pipeline._resolve_experiment_dir("AEONX1/exp")
    assert resolved == Path(pipeline.RAW_DATA_ROOT) / "AEONX1/exp"


def test_processed_zarr_path_reroots_raw_to_processed(monkeypatch):
    monkeypatch.setattr(pipeline, "RAW_DATA_ROOT", "/data/raw")
    monkeypatch.setattr(pipeline, "PROCESSED_DATA_ROOT", "/data/processed")
    bin_path = (
        "/data/raw/AEONX1/exp/2026-05-11T07-50-11/NeuropixelsV2/"
        "NeuropixelsV2_ProbeB_AmplifierData_0.bin"
    )
    out = pipeline._processed_zarr_path(bin_path)
    assert out.as_posix() == (
        "/data/processed/AEONX1/exp/2026-05-11T07-50-11/NeuropixelsV2/"
        "NeuropixelsV2_ProbeB_AmplifierData_0.zarr"
    )


def test_processed_zarr_path_rejects_path_outside_raw_root(monkeypatch):
    monkeypatch.setattr(pipeline, "RAW_DATA_ROOT", "/data/raw")
    monkeypatch.setattr(pipeline, "PROCESSED_DATA_ROOT", "/data/processed")
    with pytest.raises(ValueError):
        pipeline._processed_zarr_path("/somewhere/else/foo.bin")
