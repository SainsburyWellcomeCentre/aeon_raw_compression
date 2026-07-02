"""Packaging smoke test: the library builds into a wheel with correct metadata.

Proves the project is pip-installable. Delivery via pip is equivalent to a git
submodule for a DataJoint pipeline -- ``import aeon_raw_compression`` +
``pipeline.activate(prefix)`` is identical either way, and deferred activation
means import opens no DB connection (the tables themselves are covered by
``test_pipeline_structure``). A full clean-venv ``pip install`` + ``activate``
against ``aeondj`` is the HPC validation step, not this fast build check.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

_REPO = Path(__file__).parent.parent


def test_wheel_builds_with_correct_metadata(tmp_path):
    subprocess.run(
        [
            sys.executable, "-m", "build", "--wheel", "--no-isolation",
            "--outdir", str(tmp_path),
        ],
        cwd=str(_REPO),
        check=True,
    )
    wheels = list(tmp_path.glob("aeon_raw_compression-*.whl"))
    assert wheels, "no wheel produced"

    with zipfile.ZipFile(wheels[0]) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = zf.read(meta_name).decode()

    assert "Requires-Dist: datajoint" in meta          # deps travel with the wheel
    assert "License :: OSI Approved :: BSD License" in meta
    assert "SainsburyWellcomeCentre/aeon_raw_compression" in meta  # project URL
