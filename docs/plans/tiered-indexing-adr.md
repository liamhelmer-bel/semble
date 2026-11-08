# ADR: bounded source storage and search

Status: proposed; phase 1 storage experiments only. Human approval of this ADR
is required before feature implementation. The request to execute the original
plan authorizes this investigation; it does not supply the choices below in
advance. PRs target `fork-main`; `main` preserves the upstream baseline.

This refines [the delivery plan](tiered-indexing.md). Independent plan review:
[Claude Code Opus](../reviews/tiered-indexing-plan-opus.md). Future phases require
Opus code review and resolution of blocking findings before their PRs are approved.
No installed CLI/MCP configuration or existing cache is changed by this work.

## Decision and scope

Keep the three decimal-byte tiers and existing explicit exclusions. Files through
1,000,000 bytes retain existing chunks/BM25/vicinity behavior. Eligible files
through 1,000,000,000 bytes additionally get source references and persisted
normalized float32 vectors. Larger eligible files get manifests and bounded
literal search only. Run literal search over **both** oversized tiers so exact
identifiers are not less discoverable in middle-tier files.

Do not import the upstream performance branch wholesale. At `ab8b696`,
`src/semble/index/chunks.py:ChunkTable` packs metadata compactly but collects encoded
contents into a list and joins them into one buffer; it retains every source byte.
Reuse its file-ID/column representation ideas, not its content storage or builder.

Use a separate middle-tier backend: `numpy` fixed-width reference arrays and
float32 vector files, mapped read-only and searched in bounded blocks. No new
runtime dependency. The installed vicinity 0.4.6 uses plain `np.load` for vectors,
then `normalize_or_copy` scans all row norms. Its load path cannot provide bounded
working memory. Normalized matrices avoid a second full normalized-vector copy,
but norm computation itself still allocates corpus-sized temporaries. Existing
merge `np.vstack` also copies the matrix. Leave that backend for normal files.

Choose float32 initially: preserve the model's existing cosine behavior and avoid
making quantization error a prerequisite for source storage. The measured default
model has 256 dimensions. A 4 KiB window costs 1,024 vector bytes plus a packed
52-byte reference (4 + 8×4 + 16), plus an 8-byte vector-to-reference row ID. Best case: vectors/source = 0.25; measured
512-token splitting estimates for repeated Python, real Python, JSON and minified
JS are respectively 0.50, 0.50, 1.00 and 1.25, before references. These estimates
round each measured 4 KiB sample up to complete token windows, not corpus proofs.
Pathological splitting can exceed source size substantially: the conservative
byte-coverage ceiling is one nonempty window per byte, or 1,084 stored bytes per
source byte at 256 dimensions. Reject nonadvancing tokenizer offsets explicitly.

Opus suggested int8 and a 512 MB repository cap with **reported demotion**. Defer
quantization and the aggregate quota, rather than misdescribing that advice as
silent demotion. Approval of this ADR explicitly includes **no total repository
quota**. This does not mean writing until the disk fills: before embedding, do a
bounded tokenizer-only sizing pass using the exact proposed window splitter and
fingerprint validation. Project reference rows × 52 + vector rows × (4×dimension + 8), manifests,
normal-index staging allowance, old generations retained and duplicate content
scopes. Refuse staging if projected additional allocation exceeds free space
minus max(4 GiB, 10% of filesystem capacity). Report disabled coverage with a disk
reason and preserve any existing generation. Recheck free space before each
bounded write and fail before crossing that reserve; external writers can still
race the check, so handle ENOSPC without publishing. A changed source invalidates
the sizing pass. Report all component/obsolete-generation bytes. Global quotas
and shared vectors across content scopes remain follow-ups requiring review.

## Construction and internal contracts

`FilePolicy(soft_bytes, semantic_max_bytes)` validates positive integer settings
and soft <= hard; equal thresholds disable the middle tier. A separate scan
setting can disable literal fallback, reported as incomplete coverage. Classify
after extension/content/ignore filtering and a bounded 8 KiB binary probe.
Exclude symlinked oversized files, including paths with symlink ancestors, and
report the exclusion; they must not inflate eligible-byte counts.
Binary policy is NUL in the probe; arbitrary late binary bytes cannot be detected
without scanning the file. Invalid UTF-8 alone does not exclude a text file.

`FileRecord` stores repository ID, relative path, content category, language, tier,
size, device/inode when meaningful, mtime_ns, ctime_ns, source generation, and
reference range. Keep eligible hard-tier and zero-chunk files in the manifest.

`SourceRef` is a fixed-width little-endian structured array record: file ID u32,
start/end bytes u64 each, start/end lines u64 each, span hash blake2b-128.
Use 64-bit lines to handle multi-gigabyte newline-heavy files. IDs are
`(repository ID, file generation, window ordinal)`; array rows are private addresses,
not identities across rebuilds. An additional u64 vector-to-reference row map
allows references without vectors for unknown-token windows. The span hash detects unchanged-stat span edits;
it is integrity evidence, not proof of the entire file's semantic freshness.

`SourceBuilder.iter_batches` reads fixed binary blocks, emits references and
bounded embedding input, and appends vectors/reference records to staging files.
Never hold a full newline table, source AST, file text, or whole-file chunk list.
Windows target 4 KiB of raw bytes, with boundaries aligned to complete UTF-8
sequences where possible and replacement decoding of invalid bytes. CRLF and EOF
line accounting use raw LF bytes. Split windows further using tokenizer offsets
when they exceed 512 tokens (disable tokenizer truncation for counting); convert character offsets to raw UTF-8 byte offsets
within the bounded window, preserving coverage even for a single long line.
Greedily pack successive tokenizer spans within each 4 KiB window up to 512
tokens; the practical row bound depends on measured tokens/512 plus one tail per
window, with an exact sizing pass rather than the deliberately loose byte bound.
The 512-token cap is a relevance/batch-allocation choice, not a positional model
limit: model2vec is a static mean-pooling model. Compare against one untruncated
4 KiB window in the recall gate; changing this choice requires ADR review.
Never truncate input to satisfy the token cap. model2vec 0.10.0 also slices
characters to max_length × median_token_length before tokenization: after
verified splitting, encode with max_length=None to prevent that second loss. Acceptance tests must establish
byte/line accounting for replacement decoding before builder approval. Inspect
raw token IDs before dropping unknowns. If no tokens remain, or at least 50% are
unknown, persist the reference without a vector; report `no_tokens` or
`unknown_tokens` coverage and retain literal search. Never fill dense top-k with
identical unknown-token vectors. Sizing includes every reference and the row map.
Any changed file re-embeds that entire file, not only its changed windows. Full
coverage plus the sizing pass may take minutes of CPU per GB; the truncated spike
is not a throughput commitment.

Middle-tier construction uses one producer and one embedding consumer, without
large-file process workers. Preserve the v0.7.3 normal worker sandbox fallback.
Check cooperative cancellation between reads and embedding batches; an embedding
call is the largest non-interruptible unit. No whole-corpus `concatenate`, advanced
index copy, or vector reconstruction during load, refresh or merge. Copy unchanged
references/vectors from the previous generation in bounded blocks after schema,
model and fingerprint verification. New/changed files get new generations.

## Numeric defaults proposed for approval

| Resource | Default |
| --- | --- |
| Physical source/scanner read block | 65,536 bytes |
| Target source window / token cap | 4,096 bytes / 512 tokens, split rather than truncate |
| Embedding batch | at most 32 windows and 131,072 source bytes |
| Queued source text | 2,097,152 bytes total across the source pipeline |
| Source builders admitted per MCP manager | 1; pending request reports building/partial coverage |
| Vector scan block | at most 4,096 rows and 4,194,304 bytes; decrease rows for wider models |
| Vector bytes examined per query | 4,294,967,296 bytes across merged roots |
| Literal bytes examined per query | 268,435,456 bytes across both oversized tiers and roots |
| Large-file query wall deadline | 2 seconds shared by vector scan, literal scan and hydration |
| Hydration candidates / bytes | 64 candidates / 524,288 bytes total, at most 8,192 per hit |
| Result limit / snippet bytes | 50 / 4,096 per result |
| Literal terms | at most 8 unique terms, at most 256 UTF-8 bytes each |
| Concurrent oversized queries per MCP manager | 2; reserve at most 32 MiB working buffers each |
| Source retry | at most 1 bounded retry against an already published newer generation; never build inline |

These bounds apply to added large-file work. Model memory, normal indexes, all
stored vectors, OS page cache and aggregate repository size remain additional
costs. The 32 MiB buffer reservation bounds **anonymous working memory only**.
A mapped 4 GiB scan can expose roughly 4 GiB additional file-backed RSS in the
MCP process, and cgroup v2 accounts that page cache. This v1 design accepts that
residency risk subject to resource tests; it does not promise a total-RAM bound.
A bounded readinto backend is the fallback if target memory limits require it.
Each admitted search can attempt at most 4 GiB vector + 256 MiB literal +
512 KiB hydration reads; two searches double those cumulative I/O ceilings,
not simultaneously allocated buffers. The shared 2-second deadline can stop
a query well before its byte ceilings. Queue waiting and
build progress must be cancellable and bounded; no unbounded backlog in MCP.
Byte budgets are charged on attempted physical reads, including overlap and
retries; mapping length is not a read-budget measure. Check deadline/cancellation
between bounded blocks. Tests use injected clocks rather than timing luck.

## Retrieval and source identity

`SourceVectorStore.top_k(query, budget, selector)` yields IDs/scores and coverage,
keeping only block scores and a bounded candidate heap. Normalize each vector
batch once at build time. Never normalize a whole mapping at load time. Track
candidate IDs across roots without `vstack`. Scan sources in stable repository,
path and row order, round-robin in 64 MiB slices between files/roots. Return a
generation-bound continuation cursor on budget/deadline exhaustion so callers
can reach later rows rather than permanently rescanning the same prefix. Default
4 GiB covers one 1 GB file at all measured token densities, unlike the earlier
256 MiB proposal; it is not a guarantee for pathological input or merged corpora.
Report semantic coverage/cursor even on zero hits. Global dense ranking compares cosine
scores from the same model across normal and source tiers; do not fuse separate
per-tier dense ranks. Fuse this dense rank with normal BM25 and oversized literal
ranks using RRF with constant 60; ties use stable IDs. Run the existing normal-only
search unchanged **when there are no middle-tier vectors or literal hits in the
selected scope**. With oversized
candidates, existing normal BM25/dense candidate generation is preserved, but RRF
replaces the existing hybrid fusion, so normal results can change order. Document
and test this deliberate mixed-tier change. Same-model cosine is numerically
comparable, but longer mean-pooled windows can drift toward the corpus mean.
Mixed-tier recall and normal-result displacement are release gates; if they fail,
review a per-tier candidate quota or score calibration and amend this ADR before
release, rather than silently changing the ranking contract. Apply boosts only to the bounded candidate set, with a cap
on file-coherence contribution; never sum a million chunks' scores for one file.

`SourceReader.hydrate(ref, budget)` verifies the recorded root and path containment,
rejects symlinks (including ancestor swaps), opens a descriptor without following
links where supported, compares descriptor identity/size/mtime_ns/ctime_ns, reads
the bounded span, checks its hash and checks descriptor metadata again. POSIX
walks components with directory descriptors and no-follow opens; the Windows
implementation must verify final handle identity and containment or fail closed.
Do not claim portable race safety from `resolve()` followed by `open()` alone.
Changed/deleted/unreadable source yields unavailable coverage and a refresh hint;
never return unverified old offsets. A hash-matching span does not validate the
rest of a changed file. Ordinary freshness checks do not defend against an actor
able to rewrite both the source and cache metadata maliciously.

For v1, disable both oversized tiers for remote-URL indexes with explicit
`disabled:remote_source_lifetime` coverage; keep current normal remote behavior.
A managed pinned clone adds disk/lifecycle/security requirements and is deferred.
This is a visible limitation approved with this ADR, not a nonexistent source root. Local relocation
requires explicit rebind/rebuild and full fingerprint validation, never guessing a
new root from display paths. Related-code queries for source references use their
stored vector after fresh source validation; hard-tier origins report semantic
related search unavailable. Stats and file/language maps read metadata only. Exclude oversized file bytes
from existing file_chars/token-savings estimates; report source bytes read and
snippets returned separately. Reject path-only BM25 documents in v1 to retain
normal BM25 alignment; filename discoverability can be a separately reviewed
feature. On Windows compare volume/file ID, size, last-write and change-time
information from the handle where supported; ctime_ns alone is creation time
and is not equivalent to POSIX metadata-change time. Fail closed if the required
identity/freshness checks cannot be obtained.

`LiteralScanner.scan(records, terms, budget)` opens through the same verified
no-follow descriptor path as SourceReader. Compare fingerprints before and after
each file; buffer only bounded candidate snippets until verification and discard
all that file's snippets if it changed during scanning. It extracts ASCII identifier/word terms
deterministically from the query, folds ASCII case, removes a fixed documented
stopword set (`a`, `an`, `and`, `for`, `in`, `is`, `of`, `or`, `the`, `to`), deduplicates and truncates to the limits above. Matching is literal
substring OR across terms, not regex, token equality or Unicode case folding.
Rank by distinct matched terms then bounded match count; use stable path/byte ties.
Keep max(longest-term-minus-one, 0) bytes of overlap and LF count, never whole lines. Scan eligible
files in stable repository/path order and disclose prefix-biased partial coverage;
there is no promise that a budget-limited search reaches a hit at a huge file's EOF.
Fixtures cover EOF with an expanded test budget. No terms means report keyword
fallback unavailable rather than implying no matches.

`SearchResponse(hits, coverage)` is the internal result. Preserve existing public
`Chunk` and `SearchResult` fields, adding tier/provenance. Add an explicit
`search_with_coverage` API; legacy list-returning `search` emits a partial-coverage
warning when needed. CLI/MCP expose coverage even with zero hits. Exact output
envelope compatibility must be tested before changing existing clients. Coverage
includes source/vector bytes examined, eligible bytes, scanned/remaining files,
stale/unavailable hits and reasons (`no_tokens`, `unknown_tokens`, `bytes`, `deadline`, `cancelled`, `building`,
`disabled`, `no_keywords`, `unavailable`). Never label a truncated zero-hit scan
a complete no-match. Coverage is scoped to the selected content/ignore policy.

## Cache publication and lifecycle

Bump cache format **2 to 3** deliberately; old-layout caches remain orphaned in
place and their bytes are reported. Test upstream 0.7.3 and the fork sharing a
cache root without overwriting one another's layout. Both current load paths reject incompatible caches
before reading `chunks.json`. A format mismatch rebuilds only the requested source
into a sibling generation, preserving the previous one on failure.

Layout: `<cache-key>/generations/<uuid>/{metadata.json,files.json,normal/,
source/refs.npy,source/vectors.npy,source/vector_ref_ids.npy}`, with a small atomically replaced `CURRENT`
pointer containing a validated generation basename. Flush and validate staged
components before publication. Hold an OS-backed exclusive lock file per cache
key throughout sizing/build/publication, shared across CLI and all MCP processes;
a waiting caller gets building/partial coverage rather than starting duplicate
work. Use flock(2) shared/exclusive advisory locks on POSIX and LockFileEx on Windows
through a reviewed adapter with in-process ownership/refcounts. Do not use
classic POSIX fcntl/lockf record locks whose release-on-any-close semantics break
concurrent query pins; do not infer safety from PID/timeouts or reclaim a
live lock. Refuse cache publication on network filesystems unless the adapter
can establish equivalent lock semantics; test supported NFS behavior explicitly.
Crashes release OS locks. Atomic pointer publication is not a lock. Never replace
a mapped data file in place. Each reader acquires a shared OS generation lock
and opens every component when pinning a generation (never lazily after the pin).
Use a short cache-key catalog lock around CURRENT lookup and pin acquisition;
cleanup takes the per-key build lock, then that catalog lock plus a nonblocking
exclusive generation lock
before deletion. Keep locks outside generation directories so unlinking cannot
split lock identity. Readers pin generations across all processes; eviction closes readers
and mappings when their last active query releases them. Cancellation drops
temporary generation files. Obsolete generations are removed by explicit scoped
cleanup only after no reader holds them; Windows sharing failures leave them for
a later cleanup. No cache purge or automatic all-repository rebuild.

## Phase and acceptance gates

The original six phases remain, with Opus review and a PR to `fork-main` after
each. Literal scanner contracts are fixed here before builder work; its integration
remains phase 5 because phases 2–4 establish the shared manifests, verified reader
and coverage envelope it consumes; there is no intermediate release promising
oversized lexical coverage. CI support for `fork-main` is a precondition for the
phase-2 PR. CI must include pull requests targeting `fork-main` (the upstream
workflow currently runs only for `main`). No releases or installed-agent rollout.

Phase 1 experiments isolate baseline storage, source reads, real embedding
batches, literal reads and vector loading. They are not a feature implementation
or a claim that the acceptance suite already passes. Use the checked-in results
and limitations; later phases must provide correctness, semantic-recall and
resource evidence for the actual implementation. Float32/window recall comparisons,
strict token splitting, mid-read mutation, Windows publication, all-large corpora,
incremental transitions and concurrent eviction are release gates. If measurements
require materially different defaults, revise this ADR and obtain approval.
