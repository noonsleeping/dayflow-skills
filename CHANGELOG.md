# Changelog

## v0.5 (2026-05-15)

- **dayflow-reflect**: Add self-heal — scans the last 7 days for daily files whose corresponding review is missing, and processes them in one run alongside the target day. Designed for weekend-off workflows where Monday morning automatically backfills Friday/Saturday/Sunday reviews.
- **Schedule**: Default cron changed from daily (07:30 ingest / 08:30 verify / 09:00 reflect) to weekday-only — ingest `0 9 * * 1-5`, verify `15 9 * * 1-5`, reflect `30 9 * * 1-5`.

## v0.4 (~2026-05-08)

- **Scheduled tasks**: Initial cron registration via Claude Code's `mcp__scheduled-tasks` — `dayflow-ingest-daily` (07:30), `dayflow-ingest-verify` (08:30), `dayflow-reflect-daily` (09:00). All run every day, jitter-adjusted by the MCP scheduler.

## v0.3 (~2026-05-07)

- **dayflow-reflect**: 10-category attention classification system (内容创作 / 技术开发 / 调研学习 / 专业探索 / 商务沟通 / IM/视频会议 / 项目管理 / 碎片浏览 / 长内容娱乐 / 个人事务) with boundary rules. Hard-coded heuristics for cross-cutting cases (长视频 → 长内容娱乐, 写文档 → 技术开发, 群协作 → IM, etc.). frontmatter exposes `category_minutes` distribution for downstream trend analysis.

## v0.2 (~2026-05-07)

- **dayflow-ingest**: Removed `partial` status. Large gaps (>2h between adjacent cards) move from a status flag to metadata-only — recorded in `large_gaps[]` array in frontmatter. Self-heal now only retries `failed` or missing-from-log dates, not `partial`.

## v0.1 (2026-05-08)

Initial release:

- **dayflow-ingest**: idempotent extraction from Dayflow SQLite to daily Markdown notes; self-heal last 7 days; schema drift detection; `--all` bulk backfill mode; AUTO marker block separates auto-generated content from manual notes.
- **dayflow-reflect**: Claude-driven daily review (TL;DR + focus blocks ≥30 min + signals: rework / context-switching / long blocks). Consumes ingest output, writes to `04-workflow/dayflow/reviews/daily/`.
