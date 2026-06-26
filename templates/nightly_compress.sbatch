#!/bin/bash
#SBATCH --job-name=aeon-raw-compress
#SBATCH --partition=cpu
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=1-00:00:00
#SBATCH --output=logs/aeon_raw_compress_%j.out
#SBATCH --error=logs/aeon_raw_compress_%j.err

# Nightly raw-ephys compression for the SWC HPC.
#
# Compression is CPU-only -- unlike SpikeSorting it does NOT need a GPU, so this
# runs on the `cpu` partition. One night is usually ample; a single ~30-min,
# 384-channel chunk compresses in a few minutes, but a backlog of large chunks
# can take hours, hence the 1-day walltime. Scale --mem/--time up for big
# backlogs.
#
# Submit once:     sbatch templates/nightly_compress.sbatch
# Schedule nightly: see templates/README.md (cron on a submit host, or a
#                   self-resubmitting `sbatch --begin=now+1day` tail).

set -euo pipefail

# Every batch session starts fresh -- load modules explicitly.
module load uv

# Run from the project root. Override AEON_RAW_COMPRESSION_DIR if your submodule
# lives elsewhere (e.g. inside your analysis repo next to aeon_mecha).
cd "${AEON_RAW_COMPRESSION_DIR:-$HOME/ProjectAeon/aeon_raw_compression}"
mkdir -p logs

# --- n_jobs / CPU trap -------------------------------------------------------
# os.cpu_count() on SLURM returns ALL physical cores on the node, not your
# cgroup allocation, so any fractional/auto n_jobs (e.g. SpikeInterface's
# n_jobs=0.8 or -1) would oversubscribe badly. Compression runs single-threaded
# by default (n_jobs=1), which also sidesteps the intermittent fork+BLAS crash
# seen on this cluster. Cap the math libraries to the allocation to be safe; if
# you ever enable parallel zarr writes, pass an explicit integer n_jobs that
# matches --cpus-per-task above -- never a fraction.
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export OPENBLAS_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export MKL_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}

# Discover newly-arrived complete files, compress them to zarr, and verify the
# byte-exact round-trip. --placed-by restricts to your own triggers; drop it to
# process every project trigger. OriginalDeletion is never populated here:
# deletion is hard-disabled in source for v1.
uv run python scripts/run.py --placed-by "${USER}"
