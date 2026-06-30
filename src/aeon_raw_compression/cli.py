"""Pure helpers shared by the CLI entry points in ``scripts/``.

Kept DB-free and side-effect-free so they can be unit-tested without a database;
the scripts wrap them with argparse + ``pipeline.activate`` + insert/populate.
"""

import datetime


def build_trigger_row(experiment, placed_by, epoch="", trigger_time=None):
    """Build a ``RawEphysDiscoveryTrigger`` row dict.

    ``trigger_time`` defaults to now (it is part of the primary key); pass an
    explicit value for deterministic tests.
    """
    if trigger_time is None:
        trigger_time = datetime.datetime.now()
    return {
        "trigger_time": trigger_time,
        "placed_by": placed_by,
        "experiment_path": experiment,
        "epoch_path": epoch or "",
    }


def build_populate_restriction(placed_by=None):
    """Restrict a populate to one user's triggers, or to everything.

    Returns ``{"placed_by": ...}`` when a user is given, else ``{}`` (all
    triggers). The restriction flows through the FK chain to ``CompressedFile``.
    """
    return {"placed_by": placed_by} if placed_by else {}


def summarize_run(num_registered, num_compressed):
    """Human summary of a run + whether it should be considered failed.

    A file that registered but produced no ``CompressedFile`` row errored during
    compress/verify (errors are suppressed in the nightly run, so they would
    otherwise be invisible). Returns ``(message, failed)`` where ``failed`` is
    True when any registered file did not compress.
    """
    num_errored = max(0, num_registered - num_compressed)
    msg = f"registered={num_registered} compressed={num_compressed} errored={num_errored}"
    return msg, num_errored > 0
