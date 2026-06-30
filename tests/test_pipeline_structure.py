"""Structure tests for the DataJoint pipeline -- no database connection.

These assert the module imports DB-free (deferred ``schema.activate``), the four
tables exist with the right DJ base classes, the part table is ``RawEphysFile``,
and deletion is hard-disabled in source. The populate logic is exercised by the
HPC integration tests (Task 8), not here.
"""

import datajoint as dj

from aeon_raw_compression import pipeline


def test_four_tables_exist_with_correct_base_classes():
    assert issubclass(pipeline.RawEphysDiscoveryTrigger, dj.Manual)
    assert issubclass(pipeline.RawEphysDiscovery, dj.Imported)
    assert issubclass(pipeline.CompressedFile, dj.Computed)
    assert issubclass(pipeline.OriginalDeletion, dj.Computed)


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
