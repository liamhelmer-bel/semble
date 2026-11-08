# Opus phase-1 review, round 2

Reviewer: claude-opus-5-5 (Claude Code reported model). Author: Codex (GPT-6).
Scope: phase-1 working-tree evidence based on 3ff4086; reviewed hashes below.
Advice is not human design approval. R1/R2 and E1–E3 are incorporated in the
subsequent final revision; final review records that revision separately.

# Phase-1 review, round 2: tiered-indexing spike, ADR and results report

**Reviewer:** Claude Code (Opus 5.5). **Author of the reviewed artifacts:** Codex (GPT-6), as recorded in the results report. These are two different LLMs.

## Verdicts

- **Phase-1 code PR: APPROVE.** Two small pre-merge corrections, R1 and R2, are listed below. Neither changes a measurement, and neither needs another review round.
- **ADR: ready for human approval.** No design blockers remain. Below are recommended edits and the decisions the approver should make knowingly. This is reviewer advice only. It is not Liam's approval, and feature implementation stays blocked until Liam approves.

**Scope.** I read only the supplied text. I did not run the script. I did not inspect the vicinity or model2vec source, and I did not verify the "398 passed / 99% coverage" claim. I spot-checked the table against the JSON, and it matches. For example:
- baseline 100 KB RSS: 86,478,848 B = 82.5 MiB
- vicinity anon: 313,368,576 B = 298.9 MiB
- rg file RSS: 1,048.9 MiB
- 2,371 inventory files = 190 + 347 + 347 + 1,396 + 52 + 39

Statements that depend on library internals are marked as inferences.

---

## Resolution of round-1 findings

| # | Status | Note |
| --- | --- | --- |
| P1 docstring | Resolved | |
| P2 token truncation | Resolved | Measured 841 raw tokens per 4 KiB. The report now says "does truncate" and throughput is no longer presented as full-coverage. |
| P3 table | Resolved | `--table` uses named columns, and the exclusion rows are included. |
| P4 filesystem / shmem / tmpdir / free space | Resolved | ext4 is recorded, `RssShmem` is sampled, `--tmpdir` exists, and there is a 4 GB preflight. |
| P5 experiment outcomes + inventory | Resolved, with R2 below | |
| P6 exact peak | Partially resolved; acceptable | Live `/proc` counters and `VmHWM` are recorded before unmapping. `clear_refs` was not reset, so `VmHWM` includes startup. Both modes share the same ~41 MiB anonymous baseline, so the comparison still holds. |
| P7–P11, P14 | Resolved | |
| P12 fixture diversity | Disclosed and deferred | A real-Python token-density sample was added. Acceptable. |
| P13 one-byte overlap | Carried into the ADR and the results report as a production test | The spike's fixed 13-byte needle is unaffected. |
| A1 storage vs. searchability | Resolved | The 4 GiB budget covers a 1 GB file at all measured densities (≤1.25×). The scan order is stated, with round-robin 64 MiB slices and a continuation cursor. |
| A2 disk | Resolved | Tokenizer-only sizing pass, reserve of max(4 GiB, 10%), rechecks before each write, ENOSPC handling. The explicit no-quota approval is called out. |
| A3 size range | Resolved | Best 0.25, measured 0.5–1.25, plus a pathological bound. 52-byte reference. |
| A4 cross-process locking | Resolved in design | See E2 for two implementation hazards. |
| A5 scanner read path / symlinks | Resolved | |
| A6 ranking contract and length bias | Resolved | See E5 for one clarification. |
| Clarifications | All addressed | Format 2→3 with orphaning, coexistence test, remote disabled, delivery-order rationale, retry, Windows `ctime`, stats, path-only BM25 rejected, budget wording, CI precondition, provenance. |

---

## Phase-1 PR

### Pre-merge corrections (no re-review needed)

**R1. The commit must contain the evidence it cites.** In the session-start git status:
- `benchmarks/results/tiered-resource-diagnostics.json` is untracked, but the results report links to it.
- `docs/reviews/tiered-indexing-phase1-opus-round1.md` is untracked.
- `pyproject.toml` is modified but unstaged; it adds the `resource-benchmark` extra that the reproduction command needs.

Include all three in the PR.

**R2. The inventory is framed backwards, and may double-count.**
- All six repositories have **zero** supported-extension tracked files above 1 MB. The report concludes "No evidence supports removing either tier," which reverses the burden of proof. The accurate statement is: *the sampled repositories show no current demand for either oversized tier; the case for them rests on other grounds, which this spike does not establish.*
- Two of the "six repositories" report exactly 347 files each. This is probably one repository counted twice, for example a checkout and its worktree. That is an inference. Deduplicate, or state that it is deliberate, and give the selection criterion without paths.

### Non-blocking

- **Timings are single samples.** The 128 MB `embed` warm repeat (12.06 s) is slower than the cold run (8.95 s). Say that timings are indicative, n=1 per condition.
- **Provenance is hard-coded.** `"baseline": "3ff4086"` is a literal, and the results came from a modified working tree. Record `git describe --dirty` or the script's blob hash in the JSON.
- **`diagnostics()` has two robustness gaps.**
  - It reads `src/semble/index/index.py` relative to the current directory, so it must run from the repo root.
  - `lstat()` raises on files that are tracked but deleted.
- **"Shared-memory RSS is zero"** is really ≤4 KiB in several rows. This is a trivial wording fix.

---

## ADR

### Recommended edits before approval (not blocking)

**E1. State the memory contract for mapped vector scans.**
- The spike shows that a mapped scan leaves the whole matrix resident: about 150 MiB of file-backed memory for a 128 MiB matrix.
- With the budget raised to 4 GiB, one query can make up to about 4 GiB file-backed RSS visible in the MCP process. Under cgroup v2, that page cache counts against the cgroup's memory.
- The "32 MiB working buffers" figure is true only for anonymous memory. The ADR does say that stored vectors and page cache are additional costs, but a reader will still assume about 32 MiB.
- Either say this plainly, or choose bounded residency: `readinto` into a reused block buffer, or `madvise(MADV_DONTNEED)` after each block.

**E2. Two locking hazards to name in the ADR.**
- **POSIX record-lock semantics.** "fcntl shared/exclusive locks" is ambiguous. Classic `fcntl`/`lockf` record locks are dropped when the process closes *any* descriptor for that file. Two concurrent queries in one MCP process pinning the same generation would silently lose each other's lock.
  - Specify `flock(2)` or OFD locks (`F_OFD_SETLK`), or one descriptor per process with an in-process reference count.
- **Staging generations.** Cleanup deletes generations "only after no reader holds them". A generation still being built has no readers.
  - Require cleanup to also take the per-key build lock, or have the builder hold a lock on its staging generation.

**E3. Set a policy for windows that are all or mostly unknown tokens.**
- A 4 KiB hex window tokenizes to one unknown token. This is presumably the tokenizer's per-word length limit, which is an inference.
- Long base64, hex or minified blobs would therefore all store the *same* vector, which can crowd the dense top-k.
- Proposed default: store the reference but no vector for windows whose tokens are all or mostly unknown. Keep them searchable by literal scan, and add a coverage reason.

**E4. Tighten the pathological size bound.** "One window per byte, 1,076×" is safe but so loose it is uninformative.
- If the splitter packs greedily up to 512 tokens, windows per 64 KiB block are bounded by ⌈tokens/512⌉ plus a small constant.
- Since the preflight sizing pass is exact anyway, state greedy packing and the practical ceiling.

**E5. "No oversized candidates" means almost never.** Dense top-k always returns candidates whenever any middle-tier vectors are in scope. In practice, normal ranking is unchanged only for repositories with no middle-tier files in the selected scope. Say so directly.

**E6. Literal scanning is less complete than vector scanning.**
- Vector scans get a continuation cursor; literal scans do not.
- The measured literal scan runs at about 0.7–1.0 s per GB, both warm and with the cold-page attempt. That makes the 256 MiB budget look conservative under a 2 s deadline.
- Consider a cursor or a higher budget. Round 1 accepted the prefix bias, so this is optional.
- *Minor:* the packed 52-byte `SourceRef` places u64 fields at unaligned offsets. Consider padding to 56 bytes.

### Decisions the human approver should make knowingly

**D1. Demand.**
- The inventory found no files above 1 MB in the sampled repositories.
- The middle tier carries most of the design's complexity: splitter, sizing pass, vector store, generations, cross-process locks and ranking change.
- One alternative is to approve the literal-scan tier first, since it is cheap and covers both oversized tiers, and to defer the middle tier until demand appears. This is a product call, not a reviewer blocker.

**D2. No total quota.** The only disk protection is the reserve of max(4 GiB, 10% of filesystem capacity).

**D3. Build cost.**
- Not measured at full coverage. Truncated throughput was about 10–14 MB/s. Full coverage, plus a second tokenizer pass for sizing, suggests **minutes of CPU per GB** (estimate).
- Any edit to a large file re-embeds the whole file, because generations are per file.
- The ADR should state both points.

**D4. Token splitting is a choice, not a model limit (inference to verify).**
- model2vec is a static mean-pooled model with no positional encoding. `max_length` only truncates.
- Alternative: one vector per 4 KiB window with `max_length=None`. This gives a fixed 0.25 vector/source ratio, a trivial disk projection and no sizing pass. The cost is more dilution in token-dense windows.
- Both options fall under the existing window-recall release gate.

**D5. Accepted behavior changes.**
- Mixed-tier RRF reorders normal results when middle-tier vectors are present.
- Oversized tiers are disabled for remote URLs.

`★ Insight ─────────────────────────────────────`
- With static embedding models such as model2vec, a "token cap" is a relevance design choice, not a hardware or architecture limit. That is why D4 can drastically simplify the size model.
- Mapped memory and allocated memory are separate budgets. `np.load(mmap_mode="r")` avoids anonymous copies (the spike shows ~171 → ~42 MiB anonymous), but scanned pages still count as resident memory and against cgroup limits.
`─────────────────────────────────────────────────`

---

## Blockers vs. later gates

- **Phase-1 PR:** no blockers remain beyond R1 and R2.
- **Before any feature code (phase 2):**
  - Liam's explicit ADR approval, with D1–D5 decided.
  - CI running on PRs into `fork-main`.
  - E1–E3 written into the ADR, or explicitly waived by the approver.
- **Release gates (not ADR blockers):**
  - full-coverage embedding throughput at 1 GB
  - float32 vs. int8 and window-size recall, including D4
  - mixed-tier recall and normal-result displacement
  - strict token splitting and replacement-decoding accounting
  - mid-read mutation and the hydration/scanner verification tests
  - Windows publication and locking
  - lock-adapter semantics (E2)
  - concurrent eviction and cleanup
  - incremental transitions
  - coexistence of the 0.7.3 and fork caches in one cache root
  - cold-cache behavior under the 2 s deadline
  - the unknown-token window policy (E3)

Reviewed input SHA-256:

```json
{
  "benchmarks/tiered_resource_spike.py": "fc9790da1e0489dda90e0757e589f2ee9d36f6ec84506bd6b371fcaeb86d8262",
  "benchmarks/results/tiered-resource-spike.json": "d3b8c6153e3b50063e153d3a2badc715d4b8ad76bba3518926630954f210635d",
  "benchmarks/results/tiered-resource-diagnostics.json": "80001bce88a1fcfb755839ad54ee601fdfb6f5c01ac1a433016b1f05fd5a1ac2",
  "docs/plans/tiered-indexing-adr.md": "7a65a4954e2ee204550e33669208b9e046b0f5ee3e296d4e2494fed97f66b2ed",
  "docs/plans/tiered-indexing-spike-results.md": "2bbe3c94b9dccab3f83d1dab50337dc28afe046a527ef6202dd67cbf9e147d5e",
  "pyproject.toml": "fdaa2c86b5e285ecb3d32b2c50f64b1965636efc0ac9a8e7fd796bab1a317a9c"
}
```
