# Final Opus review: phase 1

Author: Codex (GPT-6). Reviewer: claude-opus-5-5 (Claude Code reported model).
Reviewed code commit: 05609cd50150e277e72ed5dcb0cf3e3f46a64a2f.
Invocation: claude --print --model opus --effort high, empty strict MCP config,
no requested tools or persistent session. This is model advice, not Liam's
human approval. The PR remains gated on that design decision.

The required script-hash pre-merge check passed. After review, only this record
and the directed documentation corrections were added: sizing reference-only
rows, the hex ratio, cleanup scope, baseline provenance and network lock gating.
No benchmark/runtime code changed after the measured and reviewed commit.

Validation performed by the author: 398 pytest tests pass, 99% coverage; Ruff
check/format, mypy, full opt-in benchmark and git diff whitespace checks pass.
Ripwire quality-delta has no gating regressions. Its two non-gating findings are
intentional exited-process exceptions in RSS sampling and an ignored generated
setuptools file outside the commit. Impact analysis finds no runtime imports of
this opt-in script; source/indexing/search behavior is unchanged.

# Final review: phase-1 PR `05609cd` and ADR

**Reviewer:** Claude Code (Opus 5.5). I used no tools and reviewed only the supplied text. I did not verify the pytest, Ruff or mypy claims.

## Verdicts

- **Phase-1 PR: APPROVE.** One pre-merge check is below.
- **ADR: ready for human approval.** This is reviewer advice only. Approval belongs to Liam, and feature code stays blocked until Liam approves.

## Prior findings

| Item | Status |
|---|---|
| R1: evidence committed | **Resolved.** Session-start status is clean, so nothing is left untracked or unstaged. |
| R2: inventory | **Resolved.** 190 + 347 + 1,396 + 52 + 39 = 2,024. The duplicate was removed, and the burden of proof is now stated correctly. |
| E1: memory contract | **Resolved.** The 32 MiB limit is now explicitly anonymous memory only. File-backed RSS of about 4 GiB and cgroup accounting are accepted as risks, with `readinto` as the fallback. |
| E2: locking | **Resolved.** `flock`/`LockFileEx` with refcounts is specified, and fcntl/lockf is explicitly rejected. The build lock covers sizing through publication, and cleanup takes the build lock first. Lock order (build → catalog → generation) is consistent across reader, builder and cleanup. |
| E3: unknown tokens | **Resolved.** Windows with no tokens or ≥50% unknown tokens get a reference but no vector, via the row map. |
| E4, E5 | **Resolved.** Greedy packing is stated, the bound is now 1,084 = 1,024 + 52 + 8, and the normal-only condition is stated precisely. |
| E6 | Unchanged. It was optional; prefix bias is disclosed. |
| Earlier non-blocking items | n=1 is stated. `diagnostics()` now resolves paths from `__file__` and filters `is_file()` before `lstat`. The shared-memory wording is fixed. |

**Spot-checks pass:**
- Table matches JSON: baseline 82.5/50.6/31.9, rg 1,090.9/1,049.1, vicinity 321.5/299.0, mapped 153.3/111.8.
- Live mapped counters: 195,692 kB = 191 MiB and RssFile 149.8 MiB.
- vicinity VmHWM: 319 MiB.
- Shared-memory maximum: 36,864 B = 36 KiB.

## Pre-merge check (blocker only if it fails)

The JSON records `git_describe: 3ff4086-dirty`. Provenance therefore rests entirely on the script hash. Any edit made after the rerun, including formatting, breaks it.

```
sha256sum benchmarks/tiered_resource_spike.py
```

Expected output:

`9c4f5fcbe383a64316b4b3a2592cdc50619f73fdd5cfbc281a1c6456a844d3f9  benchmarks/tiered_resource_spike.py`

## Non-blocking code notes

1. **Provenance is captured at the end of `run()`.**
   - `git describe` runs from the current directory, after the roughly 2.5 GB run finishes. Run from outside the repo, it raises and the results are lost.
   - Fix: capture it first, with `cwd=Path(__file__).parent`.
   - The literal `"baseline": "3ff4086"` is now ambiguous next to commit `05609cd`.
   - The diagnostics JSON records neither the hash nor the describe output.
2. **`advise_cold_pages` opens every `iterdir()` entry.** A subdirectory would raise `IsADirectoryError`. Current fixtures don't contain one, so this is latent.
3. **The cleanup claim is too broad.** "Cancellation also cleans up the worker group" holds for SIGINT, because `finally` runs. It does not hold for SIGTERM to the launcher. Grandchildren of a worker that exits normally are also not reaped. Narrow the wording.
4. **The `live_worker_memory` parse is brittle.** It splits on `VmPeak:`. It is acceptable for a spike.

## Non-blocking ADR notes

- **Sizing formula.** "vector rows × (4d + 52 + 8)" undercounts once reference-only rows exist. It should be references × 52 + vectors × (4d + 8). The following sentence states the intent, so tighten the formula to match.
- **Hex ratio.** The 0.25 ratio for hex in the results table contradicts E3: hex would store zero vectors. Annotate the row.
- **Embedding throughput.** The rerun's 128 MB truncated throughput is about 7.3 MB/s, below the earlier 10–14. This reinforces D3's "minutes per GB".

## Release gates (not blockers)

- **Cold-cache vector budget.** A cold 4 GiB vector budget at disk speed exceeds the 2 s deadline. The continuation cursor carries correctness, so test that the cursor actually advances under cold cache.
- **Network filesystems.** On NFS and similar, Linux emulates `flock` as byte-range locks. The lock adapter should be tested there or refuse to run.
- **E3 threshold.** Validate the 50% unknown-token threshold against real minified and base64 corpora.
- **Previously listed gates.** All prior gates stand: full-coverage 1 GB throughput, recall (float32/int8, window size, D4), mixed-tier displacement, Windows locking and publication, mutation and eviction, cache coexistence, and CI on `fork-main` before the phase-2 PR.

## Decisions remaining for Liam

D1–D5 are now each written into the ADR as explicit choices:
- D1: demand. The inventory found no files above 1 MB.
- D2: no total disk quota.
- D3: build cost and whole-file re-embedding.
- D4: splitting the token cap versus one untruncated window.
- D5: the RRF reordering and remote-source disablement.

Approving the ADR accepts all five, plus E1's residency risk.

Reviewed inputs:

```json
{
  "commit": "05609cd50150e277e72ed5dcb0cf3e3f46a64a2f",
  "sha256": {
    "benchmarks/tiered_resource_spike.py": "9c4f5fcbe383a64316b4b3a2592cdc50619f73fdd5cfbc281a1c6456a844d3f9",
    "benchmarks/results/tiered-resource-spike.json": "5815bda1c1ebcd710e48de95d450d2e1c26e2cc7ce7fafee81146e59e0cad7e8",
    "benchmarks/results/tiered-resource-diagnostics.json": "645488dc9e78519c8a5878186d045d3100d22e8925e0fc622c192b34672abce0",
    "docs/plans/tiered-indexing-adr.md": "faa5eabb32d52941350a0d19b2db8ff9937feee8ca81e3d0b182da296d7f8ec0",
    "docs/plans/tiered-indexing-spike-results.md": "55eb4a9ead558daa15db54ca8a08a518cfb9e2905e3defacda28f3820b3ca3f9",
    "pyproject.toml": "fdaa2c86b5e285ecb3d32b2c50f64b1965636efc0ac9a8e7fd796bab1a317a9c"
  }
}
```
