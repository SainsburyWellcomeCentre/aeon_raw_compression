"""aeon_raw_compression -- import-first raw ephys compression pipeline.

Do everything through this top-level namespace::

    import aeon_raw_compression as arc

    arc.activate()  # your project's DataJoint prefix
    arc.add_trigger("AEONX1/<exp>", placed_by="me")  # scope what to scan

    # Option A -- populate the exposed tables directly:
    arc.RawEphysDiscovery.populate()
    arc.CompressedFile.populate()
    # Option B -- or the one-call convenience (equivalent):
    arc.run()

    print(arc.report_deletable())  # raw files now safe to delete

Importing opens no database connection (deferred schema activation); call
:func:`activate` before populating.
"""

from aeon_raw_compression import pipeline  # keep `arc.pipeline` accessible
from aeon_raw_compression.pipeline import (
    DELETION_ENABLED,
    CompressedFile,
    RawEphysDiscovery,
    RawEphysDiscoveryTrigger,
    RawEphysFileDeletion,
    RunSummary,
    activate,
    add_trigger,
    report_deletable,
    run,
)

__version__ = "0.1.0"

__all__ = [
    "activate",
    "add_trigger",
    "run",
    "report_deletable",
    "RunSummary",
    "RawEphysDiscoveryTrigger",
    "RawEphysDiscovery",
    "CompressedFile",
    "RawEphysFileDeletion",
    "DELETION_ENABLED",
    "pipeline",
    "__version__",
]
