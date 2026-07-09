"""Editable end-to-end example: compress one experiment.

Copy this file, edit the two CONFIG lines, and run it::

    python run_compression.py

It uses your project's DataJoint config (``datajoint.json`` + ``.secrets/``, see
the README) and creates the compression tables under your project's own prefix.
Re-running is safe -- already-registered / already-compressed files are skipped.
"""

import aeon_raw_compression as arc

# --- CONFIG: edit these two -------------------------------------------------
EXPERIMENT = "AEONX1/<your-experiment>"  # a directory under the raw root, or an absolute path
PLACED_BY = "<your-name>"  # recorded on the trigger (audit / who scoped the scan)
# ----------------------------------------------------------------------------

arc.activate()  # uses your project's configured DataJoint prefix
arc.add_trigger(EXPERIMENT, placed_by=PLACED_BY)  # scope what to scan

# Option A -- populate the exposed tables directly (each step is a real table
# you can inspect, restrict, and re-populate independently):
arc.RawEphysDiscovery.populate()  # register complete raw files
arc.CompressedFile.populate()  # compress to zarr AND verify a byte-exact round-trip

# Option B -- or the one-call convenience, equivalent to the two populates above:
#     summary = arc.run()
#     print(summary)  # e.g. "registered=26 compressed=26 errored=0"

# Raw files now safe to delete (verified-compressed, still present). This only
# PRINTS them -- deletion stays manual, done on the recording computer.
print(arc.report_deletable())
