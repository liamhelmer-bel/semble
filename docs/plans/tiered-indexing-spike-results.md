# Tiered storage resource spike

Phase 1 only; no runtime indexing/search behavior changes. Raw data:
[tiered-resource-spike.json](../../benchmarks/results/tiered-resource-spike.json)
and [token density/inventory](../../benchmarks/results/tiered-resource-diagnostics.json).
Author: Codex (GPT-6); independent reviewer: Claude Code Opus.

Reproduce on Linux (requires `findmnt` and `rg`) in an isolated venv installed
with `.[dev,mcp,resource-benchmark]`:

```sh
.venv/bin/python benchmarks/tiered_resource_spike.py --tmpdir /tmp > results.json
.venv/bin/python benchmarks/tiered_resource_spike.py --table results.json
.venv/bin/python benchmarks/tiered_resource_spike.py --inventory /path/to/repo
```

Uses the already cached default model snapshot
`e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`; no model download. Cache lookup uses
huggingface_hub local-only mode and honors its cache environment settings.
Model dimension confirmed: **256**. Synthetic vectors are used except in `embed`.
Fixtures are physical writes on **ext4**, not tmpfs; sampled shared-memory RSS is at most 36 KiB.
Temporary space is about 2.5 GB; preflight requires 4 GB free. Existing user
caches are untouched. Temporary fixture directories are removed after the run.

## Measurements

RSS sampled with a minimum 5 ms sleep plus recursive process inspection, for
child plus descendants, excluding launcher. Short peaks can be missed. Linux
RssAnon, RssFile and RssShmem separate memory classes; independent maxima need
not be simultaneous. Shared pages may be counted twice across workers. PSS is
not measured. Vector workers also record live /proc counters and VmHWM before
unmapping: mapped live RSS is about **191 MiB** (150 MiB file-backed), versus
vicinity's VmHWM about **319 MiB**, mostly anonymous. The sampled mapped peak
understates its live peak; mapping alone does not cap RSS.

Repeat 0 uses fsync then POSIX_FADV_DONTNEED on fixture files before each mode;
repeat 1 is an immediate warm repeat. This is an **advisory cold-page attempt**,
not verified cache eviction or a disk throughput guarantee. Readahead, shared
libraries and model pages remain outside that control. Other fixtures are warm.
Build includes cache save; load and query are separate. Operation means the
mode's own timed operation (excluded baseline classification, reads, embedding,
or scanner subprocess); launcher/import overhead is only in raw elapsed_seconds.

| Mode / fixture | Input bytes | Repeat | Cache hint | Build s | Load s | Query s | Operation s | Excluded | RSS MiB | Anon MiB | File MiB | Shmem MiB |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| baseline  | 100000 | 0 | fsync-fadvise-DONTNEED | 0.0276 | 0.0024 | 0.0003 | — | False | 82.5 | 50.6 | 31.9 | 0.0 |
| baseline  | 100000 | 1 | warm-repeat | 0.0450 | 0.0046 | 0.0003 | — | False | 82.6 | 50.6 | 32.1 | 0.0 |
| stream  | 100000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0005 | False | 62.7 | 41.2 | 21.5 | 0.0 |
| stream  | 100000 | 1 | warm-repeat | — | — | — | 0.0005 | False | 62.6 | 41.2 | 21.4 | 0.0 |
| scan  | 100000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0016 | False | 62.7 | 41.2 | 21.5 | 0.0 |
| scan  | 100000 | 1 | warm-repeat | — | — | — | 0.0002 | False | 62.9 | 41.3 | 21.6 | 0.0 |
| baseline  | 4000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0032 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| baseline  | 4000000 | 1 | warm-repeat | — | — | — | 0.0032 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| stream  | 4000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0055 | False | 62.8 | 41.3 | 21.5 | 0.0 |
| stream  | 4000000 | 1 | warm-repeat | — | — | — | 0.0034 | False | 62.7 | 41.3 | 21.5 | 0.0 |
| scan  | 4000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0056 | False | 62.8 | 41.4 | 21.5 | 0.0 |
| scan  | 4000000 | 1 | warm-repeat | — | — | — | 0.0033 | False | 62.9 | 41.4 | 21.6 | 0.0 |
| embed  | 4000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.6245 | False | 174.8 | 140.8 | 54.6 | 0.0 |
| embed  | 4000000 | 1 | warm-repeat | — | — | — | 0.5068 | False | 175.6 | 141.7 | 54.5 | 0.0 |
| baseline  | 32000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0022 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| baseline  | 32000000 | 1 | warm-repeat | — | — | — | 0.0026 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| stream  | 32000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0234 | False | 62.8 | 41.3 | 21.5 | 0.0 |
| stream  | 32000000 | 1 | warm-repeat | — | — | — | 0.0147 | False | 62.7 | 41.3 | 21.4 | 0.0 |
| scan  | 32000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0258 | False | 62.9 | 41.4 | 21.6 | 0.0 |
| scan  | 32000000 | 1 | warm-repeat | — | — | — | 0.0141 | False | 62.9 | 41.4 | 21.5 | 0.0 |
| embed  | 32000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 4.2077 | False | 179.4 | 145.4 | 54.8 | 0.0 |
| embed  | 32000000 | 1 | warm-repeat | — | — | — | 4.0806 | False | 179.8 | 145.9 | 54.8 | 0.0 |
| baseline  | 128000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0022 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| baseline  | 128000000 | 1 | warm-repeat | — | — | — | 0.0024 | True | 77.4 | 47.4 | 30.0 | 0.0 |
| stream  | 128000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.1405 | False | 62.8 | 41.3 | 21.5 | 0.0 |
| stream  | 128000000 | 1 | warm-repeat | — | — | — | 0.0868 | False | 62.9 | 41.3 | 21.6 | 0.0 |
| scan  | 128000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.1745 | False | 62.9 | 41.4 | 21.5 | 0.0 |
| scan  | 128000000 | 1 | warm-repeat | — | — | — | 0.0494 | False | 62.8 | 41.4 | 21.4 | 0.0 |
| embed  | 128000000 | 0 | fsync-fadvise-DONTNEED | — | — | — | 17.5639 | False | 181.0 | 147.0 | 54.7 | 0.0 |
| embed  | 128000000 | 1 | warm-repeat | — | — | — | 15.2779 | False | 180.8 | 146.8 | 54.8 | 0.0 |
| baseline  | 1000000001 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.0026 | True | 77.4 | 47.4 | 30.1 | 0.0 |
| baseline  | 1000000001 | 1 | warm-repeat | — | — | — | 0.0025 | True | 77.5 | 47.4 | 30.1 | 0.0 |
| stream  | 1000000001 | 0 | fsync-fadvise-DONTNEED | — | — | — | 0.9991 | False | 62.9 | 41.4 | 21.7 | 0.0 |
| stream  | 1000000001 | 1 | warm-repeat | — | — | — | 0.5797 | False | 62.9 | 41.4 | 21.5 | 0.0 |
| scan  | 1000000001 | 0 | fsync-fadvise-DONTNEED | — | — | — | 1.0495 | False | 63.0 | 41.4 | 21.6 | 0.0 |
| scan  | 1000000001 | 1 | warm-repeat | — | — | — | 0.4788 | False | 62.9 | 41.4 | 21.5 | 0.0 |
| baseline 200-files-2-workers |  | — | — | 0.5083 | 0.0091 | 0.0004 | — | False | 205.2 | 130.7 | 74.5 | 0.0 |
| scan 1-GiB-single-line |  | — | — | — | — | — | 0.3605 | False | 62.9 | 41.4 | 21.5 | 0.0 |
| folded 1-GiB-single-line |  | — | — | — | — | — | 0.7659 | False | 62.9 | 41.4 | 21.6 | 0.0 |
| rg 1-GiB-single-line |  | — | — | — | — | — | 0.3224 | False | 1090.9 | 41.8 | 1049.1 | 0.0 |
| vicinity  | 134217856 | 0 | — | — | 0.0881 | 0.0072 | — | False | 321.5 | 299.0 | 22.9 | 0.0 |
| vicinity  | 134217856 | 1 | — | — | 0.0873 | 0.0060 | — | False | 319.2 | 297.4 | 23.0 | 0.0 |
| mapped  | 134217856 | 0 | — | — | 0.0005 | 0.0152 | — | False | 153.3 | 41.3 | 111.8 | 0.0 |
| mapped  | 134217856 | 1 | — | — | 0.0004 | 0.0139 | — | False | 161.8 | 41.3 | 121.4 | 0.0 |


## Token density and sizing

Tokenizer truncation is explicitly disabled for these counts. `encode` otherwise
sets tokenizer truncation and also slices text to max_length × median_token_length
characters in model2vec 0.10.0's `_encode_dispatch`. The spike's 512-token embedding
mode **does truncate** repeated Python (841 raw tokens per 4 KiB); throughput is
for truncated input and cannot predict full-coverage throughput. It also repeats
a small fragment, unlike diverse source. Production must split using offsets and
encode each verified bounded input with max_length=None.

| 4 KiB sample | Raw tokens | Tokens / KiB | Minimum 512-token windows | Estimated float32 vector/source ratio |
| --- | ---: | ---: | ---: | ---: |
| repeated-python | 841 | 210.25 | 2 | 0.50 |
| minified-js | 2526 | 631.50 | 5 | 1.25 |
| hex | 1 | 0.25 | 1 | 0.25 |
| json | 2016 | 504.00 | 4 | 1.00 |
| repo-python | 1004 | 251.00 | 2 | 0.50 |

Timings have n=1 per condition and are indicative, not statistical comparisons.
Raw data records the script hash and dirty git description.

Ratios above count potential vectors before the ADR’s unknown-token filter;
the all-unknown hex sample would actually persist a reference and **zero vectors**.
The raw baseline field identifies the original behavior commit, not the spike
script revision; script_sha256 binds the exact measured script.

These are small-sample sizing estimates, not full-corpus row counts. Hex produces
one unknown token: no tokenizer splitting strategy recovers semantics of arbitrary
hex identifiers with this model. References add 52 bytes per row, plus an 8-byte vector-to-reference map entry
per vector in the revised ADR. Zero-token
inputs still need an explicit coverage/reference policy; no misleading vectors.

## Outcome of Opus's requested experiments

| Experiment | Outcome / remaining work |
| --- | --- |
| 1. Real repositories | Done: five distinct selected local repository trees, 2,024 tracked supported-extension files, zero above 1 MB, 100 MB or 1 GB. No paths recorded in results. This excludes ignored/untracked assets and is an extension-based upper bound before content/binary filters, not an inventory of every repository. Selection used active local development repositories; one same-commit duplicate checkout was removed. This sample shows no current demand for either oversized tier; the product case rests on the user’s requested large-file support, which this inventory does not establish. |
| 2. Formats/window recall | Deferred to actual retrieval phase: synthetic unit vectors cannot establish recall. Float32 baseline chosen; quantization requires independent evidence. |
| 3. Vicinity allocation | Done via source inspection and RSS/live VmHWM: eager load and whole-array norm temporaries; mapped blocks avoid that anonymous allocation. tracemalloc deferred because it would not capture all native/page residency costs. |
| 4. Streaming model | Real cached model measured through 128 MB with bounded batches, plus token-density diagnostics. 1 GB full-coverage embedding deferred until the nontruncating splitter exists; extrapolating truncated throughput would mislead. |
| 5. Scanner | Done: 1 GiB single-line EOF marker found by literal and ASCII folded scans; rg reports one matching line. 64 KiB blocks chosen for this bounded-shape prototype, not an optimality claim. rg is faster here but maps about 1 GiB; folded copy remains bounded. |
| 6. Memory | Done: child plus descendants, RSS, anonymous/file/shared counters; live vector-worker counters. PSS deferred: RSS shared-page duplication is disclosed, and no total-RAM guarantee is derived from these figures. |
| 7. Cold pages | Done: fsync/fadvise first pass plus warm repeat on ext4. Advisory only; no privileged global cache drop. |
| 8. Windows | Deferred: this host is Linux; generation publication/locking tests must pass on Windows CI before release. |
| 9. Stale source | Deferred to SourceReader implementation: no production reader exists to validate; mutation/identity/hash tests are explicit release gates. |

## Interpretation and limits

- Baseline skips every oversized fixture; exclusion rows are included above.
- Stream mode emits experimental 32-byte records per 64 KiB block, proving only
  buffering shape, not the production 52-byte reference schema/window coverage.
- Literal prototype finds an EOF marker only with a full scan. Production budgets,
  filtering, verified descriptors, term extraction and snippets are future work.
  Production tests must cover one-byte terms (empty overlap) and boundary duplicates.
- The mapped prototype scans 4,096 rows and retains ten candidates. Tie relevance
  is not compared. It is not the production ranker or a zero-residency claim.
- The 200-file fixture exercises two normal workers. Real embeddings tested through
  128 MB, scanner through 1 GB; no feature Windows, incremental refresh, cancellation,
  concurrent query, eviction, corrupt-cache or semantic recall evidence is claimed.
- Linux-specific tooling is confined to this opt-in benchmark. Timeout or SIGINT
  kills the live worker group through finally; SIGTERM and orphaned grandchildren
  after normal worker exit are not covered by that cleanup guarantee.

Results support a separate block backend and small embedding batches. Concrete
choices and release gates: [ADR](tiered-indexing-adr.md).

Configured baseline pytest: **398 passed**, **99% coverage**. Script/source pass
Ruff check and mypy; script passes Ruff format. No runtime source changed.
