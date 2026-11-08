# Semble fork: coding-agent handoff

Read [the proposed tiered-indexing plan](docs/plans/tiered-indexing.md) before
changing indexing, persistence or search. This checkout is based on upstream
v0.7.3. The plan is a proposal: obtain human design approval before implementation.
Use a separate model or a fresh-context adversarial review before requesting that
approval. Liam intends to start the implementation agent separately.

Current released behavior excludes files over 1,000,000 bytes from both the index
and search results. The plan does not change that behavior. Use scoped, bounded
reads or keyword searches if an excluded file may contain the answer. Never read
an entire oversized file into agent context or raise the limit just to find a hit.

Use Beads for task tracking (`bd prime`); use the global Beads skill if needed.
This repository opts into the user's team-maintainer policy: routine commits,
quality gates, code pushes and Dolt sync are allowed once work is complete. Keep
changes atomic, use a feature branch/PR after initial setup, and mirror code to
both `origin` (GitHub fork) and `rad` (Radicle). Never push to `upstream`.

Keep machine-specific agent hooks and environments local. Do not deploy this fork
into installed CLI/MCP configurations or purge existing caches as part of feature
development. Follow upstream Python tooling and CI; Rust-specific commands in
parent instructions do not apply to this Python project.
