# Claude Code Opus plan review

Reviewer: `claude-opus-5-5` (reported by Claude Code). Original plan author model: not recorded (prior session).
Spike/ADR author: Codex (GPT-6); independent reviewer: Claude Opus.
Scope: `docs/plans/tiered-indexing.md` at `3ff4086`.
Invocation: `claude --print --model opus --effort high --output-format json`.
This is reviewer advice, not human approval.

**Verdict: APPROVE going ahead with the resource spike, provided the spike scope below is added to the spike's brief.** This is not approval of the feature design: the ADR should be REQUEST_CHANGES until blockers B1–B7 are answered. None of them needs feature code before the spike, and the spike is where they get settled.

I checked the plan against the code at `3ff4086` using read-only searches. I changed no files and took no outside actions.

## What the plan gets right
- It separates whether a file is eligible from which tier it lands in, and states that ignore rules and binary exclusions still apply.
- It admits the middle tier is not metadata-only, and that the 1 GB limit applies to semantic indexing, not to whether a file can be found.
- It reports partial coverage honestly and never claims "no match" after a cut-off scan.
- It publishes a new cache atomically and leaves the old one usable.
- It correctly treats `perf/lazy-chunk-content` as only partly useful.

## Blockers the ADR must resolve

**B1. Middle-tier vectors can take more disk than the source text they replace.**
- Chunks target 750 characters (`chunking.py:12`). A 1 GB file therefore yields about 1.33 million chunks.
- At 256 float32 dimensions (the test stub uses 256; confirm the real model's `model.dim`), each vector is 1 KiB. That is about 1.37 GB of vectors per 1 GB file, before references.
- So "no stored text" saves almost nothing, and it fails outright for files over about 730 MB.
- The ADR must choose:
  - a middle-tier window size in bytes, separate from the 750-character chunks;
  - a vector format (float16 or int8 with a per-row scale);
  - a total middle-tier vector budget per repository, with a rule for demoting files to the hard tier once it is used up.

**B2. The tiers have a lexical blind spot.**
- Middle-tier files get no lexical postings, but hard-tier files get a literal scan.
- An exact identifier is then findable in a 2 GB file but not in a 2 MB one. Semble's alpha auto-detection leans toward BM25 for identifier-like queries, which is exactly where the middle tier is invisible.
- Fix: run the bounded literal scanner over middle-tier files too, or store some compact lexical structure. The ADR has to say which.

**B3. The vector backend probably copies the whole matrix.**
- `SelectableBasicBackend` extends vicinity's `CosineBasicBackend` and loads through `super().load` (`dense.py:113-117`).
- A cosine backend usually normalises its vectors when built, which allocates a second full matrix even if the file was memory-mapped.
- Merging indexes and `create.py:214` (`previous.vectors[...]` slices put into `reused_parts`) suggest concatenation on rebuild.
- The spike must confirm this, and the middle tier almost certainly needs its own block-scanning backend.

**B4. Reading source at query time opens a security hole.**
- After indexing, a file can be swapped for a symlink to something outside the repository. The next hydration would then return that file's content to the agent.
- Required read sequence:
  1. Resolve the path under the recorded root.
  2. Open with `O_NOFOLLOW` where available.
  3. `fstat` the open descriptor and compare (device, inode, size, mtime_ns) with the manifest.
  4. Read through that descriptor.
  5. `fstat` again.
- The hard-tier scanner needs the same rules.

**B5. Freshness relies on modification time only, which is too weak once byte offsets are stored.**
- Today's manifest checks only `mtime_ns` (`cache.py:157`, `create.py:212`).
- Fingerprint each file with (device, inode, size, mtime_ns).
- Also store an 8-byte hash of each chunk's bytes (blake2b-64 from the standard library is enough) and check it during hydration. That is about 8 bytes per chunk and makes stale snippets detectable at the span level.

**B6. Git-URL repositories have nowhere to read from.**
- `index.py:248` clones into a temporary directory. If, as seems likely, it is deleted after the build, a cached index has no source left for hydration or scanning.
- Choose one: turn off the middle and hard tiers for remote repositories, or keep the clone for the index's lifetime and count it against a disk budget.

**B7. Atomic replacement fails on Windows while the cache is mapped.**
- Upstream CI runs on Windows, where a memory-mapped file cannot be renamed over or deleted.
- Use generation directories with an atomically replaced `CURRENT` pointer file. Remove old generations only when no reader holds them, for example at the next startup.

## Other design gaps
- **Ranking.** Both tiers use the same model, so dense cosine scores are directly comparable across tiers. The plan's "incomparable raw scores" applies only to the combined hybrid score.
  - Build one dense ranking across both tiers. Middle-tier chunks simply have no BM25 rank.
  - Running reciprocal rank fusion over two separate per-tier lists would let middle-tier noise take about half of every top-k list, even for irrelevant queries.
  - Cap the file-coherence boost (`boosting.py:113`). As written, one file with a million chunks would win the per-file score sum every time.
- **Duplicate indexes per content scope.** Each content selection gets its own index (`cache.py:37-41`), so a 1 GB file indexed under both `code` and `all` is embedded twice. Share middle-tier vectors keyed by (file fingerprint, model, window settings), or count the duplication against the budget.
- **Builds inside MCP requests.** The first query builds the index inline, so a 1 GB embedding job stalls a tool call. The MCP cache limit `_CACHE_MAX_SIZE = 10` (`mcp.py:31`) counts indexes, not bytes. Specify a build-time budget or a background build that returns partial results.
- **Cancellation in workers.** `ProcessPoolExecutor` cannot cancel tasks that are already running. You need cooperative cancellation, such as a shared event checked once per window.
- **Classifying files without opening them.** Binary detection needs a bounded read of the first few KiB; stat metadata alone is not enough. State this explicitly.
- **Stats.** `stats.py` adds whole-file sizes to `file_chars`, so savings would look hugely inflated for middle-tier hits. Cap the figure or exclude these files.
- **Cheap filename discoverability.** Indexing only the path of hard- and middle-tier files in BM25 gives filename search at near-zero cost. It is a path document, not an empty one, so it is consistent with the plan's rule against empty BM25 documents.
- **Off switches.** Allow `soft == hard` to disable the middle tier, and offer a separate switch for the scan tier.
- **Delivery order.** Consider building the bounded literal scanner (stage 5) before middle-tier semantic search (stages 3–4). The scanner covers both oversized tiers with no stored data and fixes B2, and it is the simplest piece to bound.

## Proposed starting budgets (the spike should confirm or replace them)

| Item | Starting value |
| --- | --- |
| Middle-tier window | 4 KiB of bytes, split at a newline if one falls within the last 512 B. Test 750 / 2 K / 4 K / 8 K for recall. |
| Vector format | int8 plus a float32 scale per row: about 260 B per window. A 1 GB file is then about 244 k windows, roughly 63 MB of vectors plus about 9 MB of references. |
| Reference record | Fixed layout: file id u32, start/end byte u64×2, start/end line u32×2, span hash u64. About 36 B. |
| Middle-tier vector budget per repository | 512 MB on disk. Once exceeded, demote files to the hard tier in a fixed order and report it. |
| Dense scan | Blocks of 64 k rows (about 16 MB), keeping a top-k heap. Per-query heap stays within one block plus the heap. |
| Build in-flight limit | 64 MiB of text across all workers. Embedding batches capped by bytes (8 MiB of text) as well as `_EMBED_BATCH_SIZE`. |
| Build memory target | Anonymous memory growth stays roughly constant whether the file is 10 MB or 1 GB, and under 200 MB above baseline. |
| Hard-tier scan per query | 256 MiB scanned, 2 s wall time, 1 MiB reads, block overlap of the longest term minus one, terms capped at 256 B, at most 50 hits, 4 KiB per snippet. |
| MCP admission across queries | At most 2 concurrent scans; at most 512 MiB of scan bytes in flight. |
| Hydration | At most top_k × 16 KiB of reads, with one retry. |

## Experiments to add to the spike
1. **Inventory real repositories first.** Count eligible-extension files above 1 MB, 100 MB and 1 GB in Liam's actual repositories. If the hard tier is nearly empty, the design can be simplified.
2. **Vector size and format against recall.** Compare float32, float16 and int8, and window sizes of 750 characters to 8 KiB. Use corpora that resemble middle-tier files: minified JS, generated code, SQL dumps, large JSON and logs.
3. **vicinity backend behaviour.** Use `tracemalloc` and RSS deltas to check whether load and normalisation copy the matrix, and whether queries need it fully resident.
4. **Streaming embedding.** Measure throughput, RSS and tokenizer allocation per batch for model2vec on 1 GB, including one single-line file.
5. **Scanner.** Compare `bytes.find` over 1 MiB blocks (and a case-folded copy per block) with `rg` on a 1 GB single-line file. Record throughput and peak RSS.
6. **Memory accounting.**
   - Read `RssAnon` and `RssFile` from `/proc/<pid>/status`, and PSS from `smaps_rollup`, sampled for the parent and every worker.
   - `RUSAGE_CHILDREN ru_maxrss` gives the largest single child, not the sum, so it cannot measure aggregate worker memory.
7. **Cold-cache runs without root.** Drop pages per file with `os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)`. This avoids `drop_caches`, which would need a sudo script for Liam.
8. **Windows mapping.** Hold a memory map while publishing a new generation and confirm the swap works.
9. **Stale detection cost.** Measure how long span-hash checks add per hydrated hit, and test behaviour when a file is written mid-read.

## Proposed interfaces
- **Cache layout:**
  - `cache/<key>/gen-<id>/`, containing:
    - `metadata.json`
    - `bm25/`
    - `dense/`
    - `source/refs.npy` (fixed record layout)
    - `source/vec.i8.npy` and `scales.f32.npy`
    - `files.json` (per-file tier and fingerprint)
  - A `CURRENT` pointer file that is replaced atomically.
- **Search results:**
  - Each hit gets additive fields: `tier` (`full`, `source` or `scan`) and `provenance`.
  - Each response gets one `coverage` object: `{scanned_bytes, eligible_bytes, files_skipped, stale_hits_dropped, truncated_reason}`.
- **Configuration:** the plan's proposed settings, plus `SEMBLE_SOURCE_VECTOR_BUDGET_BYTES` and per-query scan budget settings. Fold all of them into the cache compatibility check.

## Housekeeping
The plan notes Beads was read-only (`embeddeddolt: store is read-only`). The spike agent should repair that through the supported Beads workflow before relying on claim or close.
