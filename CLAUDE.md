# Aeon Standalone Compression Pipeline

## Task
Design and build a small standalone DataJoint library that compresses incoming raw ephys data files. Task 3 of 3 for the Aeon compression work. Spec (`raw-ephys-compression-spec.md`) was reviewed by Thinh (2026-06-16/24) and largely approved; his feedback + the 2026-06-18 meeting decisions are folded in. Next: Elissa reviews the revised spec, then greenlights implementation. **Do not start implementation until she says go.**

## Decision Context (Aeon team meeting 2026-06-11)
Three approaches were discussed for ongoing compression of new data:

1. **Table in existing pipeline** — rejected. aeon_mecha is submoduled per-user, so each user would have to set up compression themselves.
2. **Standalone non-DJ process** — rejected. Loses DataJoint's job tracking, error handling, and logging.
3. **Standalone mini DJ pipeline** — chosen, then refined (see below): a standalone *library* run per-user via submodule first, with a path to a centralized deployment later.

## Finalized Decisions (Thinh review + 2026-06-18 meeting)
- **Trigger-table design.** Discovery is driven by a `RawEphysDiscoveryTrigger` (Manual) that scopes which directories to scan and records **who placed it** (name + timestamp + path), for audit and a possible future shared-schema migration. `RawEphysDiscovery` (Imported) runs the scan in `make()` with `RawEphysFile` as a Part table.
- **Per-user submodule first, centralized later.** v1: users submodule this library next to aeon_mecha and run it on their own data/quota (Adrian, Rokas first). Reasons: per-user compute quotas; data control. Future: centralized cron on the DataJoint database server (no SLURM; idle when no triggers). Same library, both modes; don't hard-couple to aeon_mecha.
- **Per-project tables on aeondj.** New work uses the `aeondj` server (aeon-db is legacy; connecting needs the `cryptography` package). v1 creates the compression tables under each project's own DataJoint prefix (no separate shared schema/prefix — that's impractical with the per-project submodule credential setup). Tracking is per-project; a shared/central schema is deferred and would require porting per-project records.
- **Repo hosting:** SainsburyWellcomeCentre org, **public** repo. No remote until Elissa says to create it.
- **SLURM nightly template** is part of the deliverable (compress each day's recordings overnight).
- **Companion aeon_mecha PR (required, separate from #589):** PreProcessing must find + read the compressed zarr files; the discovered-and-read connection is part of the tests. Hard prerequisite before deletion is enabled.
- Compress + verify are one atomic table; optional deletion off by default.

## Key Requirements
- Standalone library, decoupled from aeon_mecha (own schema; reads raw files + Metadata.yml directly)
- Runs per-user via submodule first (own quota, own data control); centralized cron deployable later from the same library
- DJ job tracking, error handling, logging
- Optional delete mechanism (off by default, ready to enable; per-user decision)
- SLURM nightly automation template + setup instructions included

## Long-term Context
Open Ephys Bonsai ONIX1 DataFrame writer supports compression at acquisition time, but SpikeInterface can't natively read those compressed files yet. Chris Halcrow (joining Aeon team late July, also part-time SpikeInterface maintainer) will advocate for native SI support. This standalone pipeline is the medium-term solution until acquisition-time compression is viable.

## What This Pipeline Compresses
Raw ephys data files (`*_AmplifierData*.bin`, the ONIX/Bonsai NP2 naming) that come in from recordings. These are large binary files (several GB each) that benefit significantly from compression (~1.95x ratio based on earlier testing). Note: Aeon NP2 data has no separate `.ap.bin`/`.lf.bin` (SpikeGLX) bands.

## Related Work
- Compression spec and testing plan: see `aeon_mecha_compression-spec` worktree (branch `es/compression-spec`)
- The intermediates compression PR (#589) handles compression of intermediate files WITHIN aeon_mecha. This standalone pipeline handles incoming RAW data compression as a separate concern.
- The compression report is finished and was presented to the team on 2026-06-11.

## Spec File
See `raw-ephys-compression-spec.md` in this directory for the current draft spec.

## Environment
- Its own small DJ library, not part of aeon_mecha; submoduled into users' analysis repos for v1
- Connects to the `aeondj` database server (aeon-db is legacy); tables live under each project's own prefix (per-project), not a shared schema. Needs the `cryptography` package to connect.
- Runs on the SWC HPC (per-user, SLURM nightly template); future option: cron on the DataJoint database server
- Destined for the SainsburyWellcomeCentre org as a public repo (no remote yet)
- Aeon is currently on DJ 2.2.2
