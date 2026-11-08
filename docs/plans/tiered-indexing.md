# Tiered indexing with bounded memory and source-backed results

Status: proposed design; no implementation has been authorized by this document.
Prepared 2026-11-08 for the next coding agent. Obtain Liam's design approval before
feature implementation; starting an agent to review the plan is not approval.

## Goal and agreed behavior

Keep large source files searchable without retaining their text in the index.
Use decimal bytes, matching Semble's existing default:

| File size | Persistent representation | Search behavior |
| --- | --- | --- |
| Up to and including 1,000,000 bytes | Existing chunks, lexical index and embeddings | Existing hybrid search |
| Above 1,000,000 through 1,000,000,000 bytes inclusive | File metadata, chunk byte offsets, line ranges and embeddings; no stored chunk text or content-based lexical postings | Semantic retrieval followed by bounded reads of matching source spans |
| Above 1,000,000,000 bytes | File metadata only | Bounded keyword search directly from disk; no embeddings or persistent text |

The 1 GB threshold is a hard cap on **semantic indexing**, not on discoverability.
The middle tier is not literally metadata-only: embeddings are derived content,
consume disk, and are required for semantic retrieval without embedding an entire
file on every query. Do not promise semantic search from filenames alone.

Explicit `.gitignore`, `.sembleignore`, extension/content filters and binary-file
exclusions remain exclusions. Size fallback must not circumvent them. Preserve the
existing default 1 MB setting until the feature is implemented and tested.

## Starting point and reuse

Fork: https://github.com/liamhelmer-bel/semble

Upstream: https://github.com/MinishLab/semble

Baseline: v0.7.3 / `a1ba6c18a4b0a58a3d1a15217d64bc92678d1d9d`, upstream main
at plan preparation. Local checkout: `/home/openclaw/repos/semble-fork`.
`origin` is the fork, `upstream` is MinishLab. Radicle mirror:
`rad:z3nD9pZvWyXkS5uyj9LRZc5ab6sos` (remote `rad`).

Inspect `upstream/perf/lazy-chunk-content`, commit `ab8b696`, before building a
chunk table. Its `ChunkTable` provides compact arrays, lazy `Chunk` construction
and cache integration. It still stores all text in a UTF-8 buffer and constructs
that buffer from collected chunks, so it does not solve source-backed storage or
bounded construction. Port useful pieces selectively after comparing against main;
do not blindly merge an older performance branch.

Useful existing seams:

| Location | Current behavior / required change |
| --- | --- |
| `src/semble/index/files.py`: `MAX_FILE_BYTES`, `get_file_status`, `read_file_text` | Files over 1 MB are skipped; text reader loads the whole file. Separate eligibility from tier classification. |
| `src/semble/index/create.py`: `_stat_files`, `_chunk_file`, `_chunk_files`, `create_index_from_path` | Oversized files disappear; workers return full per-file lists; builder accumulates chunks and embeddings. Add a fallback manifest and bounded streaming construction. Preserve v0.7.3 sandbox worker fallback. |
| `src/semble/chunking/chunking.py`: `chunk_source` | Whole-source AST/line chunking. Introduce a bounded large-file path; character positions are not raw UTF-8 byte offsets. |
| `src/semble/index/types.py`, `src/semble/types.py` | Cache format 2, chunk-bearing manifests, public materialized `Chunk` results. Add internal chunk references and separate file metadata. |
| `src/semble/cache.py`: `load_previous_index`; `src/semble/index/index.py`: load/save | Both load full `chunks.json`; migration must cover both paths and incremental reuse. |
| `src/semble/search.py`, `src/semble/ranking/boosting.py` | Results keyed by content-bearing `Chunk`; boosting can examine all chunks. Rank by stable internal IDs before materializing bounded hits. |
| `src/semble/utils.py`: `resolve_chunk`; index statistics and related-code search | Avoid iterating a lazy table in ways that read the entire corpus. |
| `src/semble/mcp.py` | Existing idle eviction is time-based, not a byte limit. Bound concurrent work and release mappings/readers on eviction. Confirm exact module entry points before editing. |
| `benchmarks/token_efficiency.py`: `_rg_matches`, `_grep_keywords_file_units` | Reuse query/keyword ideas where useful; benchmark subprocess code is not automatically a bounded production reader. |

## Proposed internal design

### File policy and configuration

Keep `SEMBLE_MAX_FILE_BYTES=1000000` as the soft threshold for full-content
indexing; introduce `SEMBLE_SEMANTIC_MAX_FILE_BYTES=1000000000` as the upper
semantic threshold. These names and CLI equivalents are proposed interfaces, not
existing settings. Validate positive values and soft <= hard. Document the change
in meaning of the existing limit: files previously skipped for size may now be
represented in a new tier. Include both thresholds in cache compatibility checks.

Classify eligible files from stat metadata before opening them. Keep a manifest
for every eligible file, even if the corpus has zero normally indexed chunks.
Manifest fields include relative path, size, modification fingerprint, language,
content category, tier and chunk/index range. Preserve repository identity/root
through merged indexes so byte reads cannot target a different repository.

### Compact references and bounded construction

Use stable chunk IDs with file ID, byte start/end, start/end line and language.
Retain public `Chunk` / `SearchResult` text at the API boundary. Store middle-tier
references and vectors separately from normal-tier BM25 document ordering, rather
than inventing empty BM25 documents to satisfy current alignment assumptions.
Choose a compact versioned representation using existing dependencies where
possible. Inspect the upstream chunk table first; measure resident memory rather
than assuming an archive or memory map is zero-copy.

For middle-tier files, stream bounded binary blocks, maintain raw byte offsets
and line counts, decode only bounded windows, and batch embedding work. Do not
read the whole file, build a full newline-offset array, run a full-file syntax
tree, accumulate every chunk's text, or return whole-file chunk lists from workers.
Handle boundaries inside multi-byte UTF-8 sequences, invalid bytes, CRLF, EOF and
extremely long single lines. Ensure a window too long for the embedding model is
split to the supported token budget; disclose any intentional loss of coverage.

Write vector/reference batches to temporary storage and publish atomically after
validation. Bound queued work by bytes as well as item count across all workers;
one 1 GB input must not defeat the queue limit. Add cancellation and clean up
partial outputs. Preserve bounded incremental reuse instead of reconstructing all
old chunks/vectors in RAM.

Persist vectors in a form that supports bounded retrieval. Spike whether the
current vicinity backend copies them or requires a resident full matrix. If so,
use a separate middle-tier backend that scans mapped vectors in bounded blocks
and maintains top-k candidates. No `np.concatenate`/whole-corpus copy during load,
merge or refresh. Account for OS page cache separately from process heap and RSS.

### Retrieval, freshness and fallback

Search the normal hybrid index and middle-tier semantic index, rank by stable IDs,
then hydrate only a bounded candidate set for snippets/boosting. Apply reciprocal
rank fusion or another documented rank-based merge so incomparable raw scores
are not summed. Keep existing normal-file relevance tests intact. Audit boosting,
stats, `find_related`, merged indexes and incremental builds for hidden whole-table
materialization. Define related-code behavior for source-backed references and
clear unavailability for files without semantic embeddings.

Resolve a reference relative to its recorded root; validate source identity and
fingerprint before and after bounded reads. A changed, deleted, moved or unreadable
file must not produce a confident stale snippet using old offsets. Invalidate and
schedule refresh or report the hit unavailable/partial; allow at most one bounded
retry. Include source tier/provenance in additive result metadata, preserving
existing output fields and client compatibility.

For the hard-cap tier, derive explicit keyword terms deterministically from the
query and stream eligible files. Natural-language semantic intent is not equivalent
to literal keyword search: document this coverage limit. Define term matching,
case handling, binary detection, ranking and snippet limits in tests before coding.
Use fixed-size reads and bounded cross-block overlap for literal terms; do not use
unbounded regex/backtracking or whole-line buffering. Keep true byte/line locations
while truncating output around matches. If `rg` is used, use argv without a shell,
timeouts/cancellation and bounded output, and prove single-line memory behavior.
`--max-columns` alone does not establish a memory bound. A small streaming literal
scanner is preferable if a subprocess cannot meet the bound.

Set explicit per-query byte/time/result budgets, and share an admission budget
across concurrent MCP queries. The implementation design must choose and document
numeric defaults after the resource spike. Reaching a budget returns honest partial
coverage and counts/reasons; never claim a complete no-match after a truncated scan.
Keep query cancellation responsive, including source reads and child processes.

## Cache lifecycle and resource accounting

Bump cache format and rebuild incompatible caches deliberately. Do not try to read
old gigantic `chunks.json` just to migrate metadata. Publish a complete new cache
atomically, leaving the previous cache usable on failure; provide an explicit,
scoped cleanup command/instructions after successful validation. Do not delete the
user's existing cache or rebuild every repository automatically during rollout.

Incremental changes must handle movement across all three tiers, model/config
changes, ignored files, deletion, renames and corrupted/missing cache components.
Check fingerprint and schema before reusing vectors or offsets. Release workers,
readers, mappings and references on eviction/shutdown. Keep the existing 30-second
agent MCP idle configuration; the embedding model may remain resident.

Per-file limits and idle eviction are not an aggregate RAM or disk quota. Many
small files and stored vectors can still be large. Report cache bytes by component,
file/chunk counts by tier, active work and fallback coverage. Resource budgeting
must include rebuild peak, worker queues, vector backend and concurrent searches.
A configurable total cache quota/retention policy is a separate follow-up unless
approved into scope; do not imply this feature guarantees a 1 GB process limit.

## Ordered delivery and approval points

1. **Resource and backend spike.** Record cold/warm RSS, peak aggregate worker RSS,
   cache bytes and latency for normal, middle and hard-cap fixtures. Inspect the
   upstream lazy branch and vicinity vector loading. Propose actual memory/query
   budget defaults and stable interfaces. Produce a small ADR recording the storage
   and backend decision. Have a different model or a fresh-context adversarial
   reviewer examine the design, then obtain human approval before feature work.
2. **Policy and schema.** Add tier classification, configuration validation,
   fallback manifests and cache compatibility/versioning. Make all-large-file
   repositories valid. Keep default normal-file behavior covered.
3. **Source-backed builder and persistence.** Add streaming chunk/reference
   creation, bounded embedding batches, compact storage and incremental reuse.
   Prove construction does not retain the full middle-tier text.
4. **Semantic retrieval and hydration.** Integrate bounded vector retrieval,
   stable IDs, ranking, source freshness, related-code behavior and merged roots.
5. **Keyword fallback and reporting.** Implement bounded scanning, concurrency and
   cancellation limits, partial-result semantics, statistics and CLI/MCP output.
6. **Rollout and documentation.** Run regressions and resource benchmarks, document
   migration/cleanup and agent coverage rules, and review the diff independently.
   Submit a feature branch/PR to the fork. Deployment into either agent's MCP
   configuration is a separate reviewed rollout, not part of writing this plan.

These are delivery stages, not a second task tracker. Use Beads for claims,
dependencies, acceptance results and follow-up work. Respect any applicable shared
queue review protocol if the work is later submitted to that queue.

## Acceptance evidence

Use small configurable thresholds in routine tests; do not create 1 GB test files
in every CI run. Add opt-in resource tests with representative real sizes and
sparse/stream-generated fixtures, recording whether physical bytes were scanned.

- Exact boundary cases at soft-1/soft/soft+1 and hard-1/hard/hard+1; ignored and
  binary files remain excluded. An all-middle/all-hard corpus loads and searches.
- A middle-tier semantic hit whose query differs from its literal text survives
  process restart, returns the right source snippet/lines, and stores no text copy
  in its persistent chunk payload. Verify persisted vectors, not query-time full
  re-embedding. Normal-tier semantic/lexical results retain expected relevance.
- Hard-tier literal matches near file start, chunk boundaries and EOF return correct
  locations. Extremely long single lines and invalid UTF-8 do not grow buffers with
  input size. No semantic vectors exist for that tier.
- Modified/replaced/deleted files, mid-read writes, tier transitions and relocated
  roots never return stale text as current. Failed/cancelled rebuilds preserve the
  last complete index; corrupt caches trigger an actionable rebuild path.
- Concurrent queries, workers and merged indexes respect chosen byte/time budgets;
  no corpus-wide hydration through boosts, stats, related-code lookup or refresh.
  Budget exhaustion is observable in CLI/MCP results rather than a silent no-match.
- Benchmark cold build, warm load, incremental rebuild, query and idle eviction on
  the same corpus before/after. Publish RSS for parent plus workers, peak memory,
  cache component sizes, throughput and latency. Demonstrate bounded text memory
  as a single file grows; distinguish vector/index growth from text buffering.

Start with `tests/index/test_create.py`, `tests/index/test_index.py`,
`tests/test_cache.py`, `tests/test_chunker.py`, `tests/test_git.py`,
`tests/test_cli.py`, `tests/test_mcp.py` and `tests/test_stats.py`. Confirm exact
existing fixtures and commands from `pyproject.toml` and CI before adding tests.
Upstream CI installs `.[dev,mcp]` and runs pytest on Linux/macOS/Windows with
Python 3.10–3.14; run focused tests during development and the full relevant suite,
Ruff/type checks as configured before review. No feature tests were run for this
document-only preparation.

## Handoff to the coding agent

Read this plan and `AGENTS.md`, run `bd prime`, inspect git status/remotes and verify
the upstream baseline. Review the plan first; return the numeric budgets, backend
spike and design questions to Liam before implementation. Do not treat the fork,
this document or a Beads issue as proof that the feature design has been approved.
Do not change installed Semble/MCP settings or delete old caches during development.

At preparation time Beads initialization required shared-server recovery and issue
updates reported `embeddeddolt: store is read-only`; issue `semble-fork-6xl` was
created for this preparation but could not be claimed/closed. Repair through the
supported Beads workflow before depending on local issue mutations; do not edit a
native database directly to bypass the failure.

## Primary references

- [Upstream release v0.7.3](https://github.com/MinishLab/semble/releases/tag/v0.7.3).
- [Upstream lazy chunk branch](https://github.com/MinishLab/semble/tree/perf/lazy-chunk-content).
- [ripgrep discussion of single-line memory](https://github.com/BurntSushi/ripgrep/discussions/2821)
  and [related large-line issue](https://github.com/BurntSushi/ripgrep/issues/608).
