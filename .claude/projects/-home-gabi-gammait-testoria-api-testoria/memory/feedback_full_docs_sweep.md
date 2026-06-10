---
name: Always do the full Phase 5 docs sweep in one pass
description: After implementing a plan, read and update ALL docs listed in CLAUDE.md Phase 5 table — don't stop at the obvious ones
type: feedback
---

After finishing code + tests for a plan, always do the complete docs sweep in one pass before declaring done. Read every file in `docs/00-meta/`, `docs/01-product/`, `docs/02-architecture/` (including `backend/*.md`), `docs/03-engineering/`, `docs/05-quality/`, `docs/06-generated/`, and `docs/08-decisions/` to check if they need updating.

**Why:** User had to prompt a second time to get docs/00-meta, docs/01-product/index.md API surface table, and docs/02-architecture/backend service inventory updated. The CLAUDE.md Phase 5 table and AGENTS.md both spell out the full list explicitly — skipping any of them means incomplete work.

**How to apply:** After Phase 4 (quality check) passes, systematically go through every row of the Phase 5 table in CLAUDE.md. Read each referenced doc file, compare against what changed, and update. Don't declare the plan complete until every applicable doc is verified current.
