**English** | [中文](README.zh-CN.md)

# dayflow-skills

> Two Claude Code skills that bridge [Dayflow](https://github.com/JerryZLiu/Dayflow) desktop activity timelines into your wiki — automatic ingest + AI-powered daily reflection.

---

## ⚠️ Requires Dayflow

These skills consume the SQLite database produced by **[Dayflow](https://github.com/JerryZLiu/Dayflow)** — a local-first macOS desktop activity tracker by [@JerryZLiu](https://github.com/JerryZLiu) that records your screen at 1 FPS and uses an LLM to summarize what you did every 15 minutes.

**You must install and run Dayflow first.** Without it there is no data to ingest. Download Dayflow here: <https://github.com/JerryZLiu/Dayflow>

---

## What's inside

| Skill | Role | Files |
|---|---|---|
| **dayflow-ingest** | Pull Dayflow timeline cards from SQLite into structured daily Markdown notes. Idempotent writes, self-heal across last 7 days, schema drift detection, `--all` bulk backfill. | `SKILL.md` + `ingest.py` |
| **dayflow-reflect** | AI-powered daily review based on the ingested timeline. 10-category attention classification, focus blocks, signals (rework / context-switching / long blocks). | `SKILL.md` + `compute.py` |

The two skills are designed to work together: **ingest** writes raw daily notes from Dayflow's SQLite database; **reflect** consumes those notes to produce a structured daily review.

---

## Why these skills

Dayflow already does the heavy lifting — local recording, AI summarization of what you actually did. But the output lives in Dayflow's own SQLite database. These skills:

1. Move that data into a **plain-text wiki you own and can grep**
2. Add **AI-powered daily reflection** on top — categorizing your time across 10 attention types, surfacing focus blocks and rework patterns

Designed as the canonical "external data source" pattern for **[LinkcOS](https://github.com/noonsleeping/linkc-os)** (an AI-maintained personal wiki), but works standalone — path constants are easy to edit.

---

## Install

These are [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skills. Pick user-scope or project-scope install:

**User scope (any project):**

```bash
git clone https://github.com/noonsleeping/dayflow-skills.git
cp -r dayflow-skills/dayflow-ingest ~/.claude/skills/
cp -r dayflow-skills/dayflow-reflect ~/.claude/skills/
```

**Project scope (LinkcOS-style):**

```bash
cp -r dayflow-skills/dayflow-{ingest,reflect} <your-project>/.claude/skills/
```

Restart Claude Code — both skills will appear in the skill list.

### Path defaults

The skills assume your wiki lives at `~/linkc-os/`. To use a different location, edit the path constants at the top of `dayflow-ingest/ingest.py`:

```python
WIKI_ROOT = HOME / "linkc-os" / "04-workflow" / "dayflow"
WIKI_LOG = HOME / "linkc-os" / "02-wiki" / "log.md"
```

A future release may add `LINKCOS_ROOT` environment variable support.

### Dayflow database path

Default: `~/Library/Application Support/Dayflow/chunks.sqlite` (auto-detected). Override:

```bash
DAYFLOW_DB_PATH=/custom/path/chunks.sqlite python3 ingest.py
```

---

## Usage

### dayflow-ingest

```bash
# Extract yesterday + self-heal last 7 days (default)
python3 dayflow-ingest/ingest.py

# Specific date
python3 dayflow-ingest/ingest.py 2026-05-06

# One-time backfill: all distinct days from SQLite
python3 dayflow-ingest/ingest.py --all
```

Or in Claude Code: `/dayflow-ingest [YYYY-MM-DD]`

**Output:** `04-workflow/dayflow/daily/YYYY-MM-DD.md` — one file per day, with timeline cards rendered as `### HH:MM–HH:MM · title [category]` blocks. Idempotent: AUTO marker block is replaced; manual notes outside the marker are preserved.

### dayflow-reflect

In Claude Code: `/dayflow-reflect [YYYY-MM-DD]` (default: yesterday)

The skill is Claude-driven — it reads the daily ingest file, reclassifies each card across 10 attention categories (内容创作 / 技术开发 / 调研学习 / 专业探索 / 商务沟通 / IM/视频会议 / 项目管理 / 碎片浏览 / 长内容娱乐 / 个人事务), computes focus blocks, and writes a daily review.

**Output:** `04-workflow/dayflow/reviews/daily/YYYY-MM-DD.md` — structured frontmatter with `category_minutes` distribution, focus blocks, and Claude-generated TL;DR + signals.

### Scheduled tasks (optional)

For full automation, register scheduled tasks via Claude Code's `mcp__scheduled-tasks` (or use the [/schedule](https://docs.anthropic.com/en/docs/claude-code) skill):

| Task | Cron | Purpose |
|---|---|---|
| `dayflow-ingest-daily` | `30 7 * * *` | Daily ingest of yesterday + self-heal |
| `dayflow-ingest-verify` | `30 8 * * *` | Verify and re-run if 07:30 missed |
| `dayflow-reflect-daily` | `0 9 * * *` | Generate yesterday's review |

---

## Output structure

```
04-workflow/dayflow/
├── daily/
│   └── YYYY-MM-DD.md            ← raw daily notes (dayflow-ingest)
├── reviews/
│   └── daily/
│       └── YYYY-MM-DD.md        ← AI daily review (dayflow-reflect)
└── meta/
    ├── ingest-log.md            ← status table + run history
    └── schema-snapshot.md       ← Dayflow SQLite schema snapshot for drift detection
```

---

## License

[MIT](LICENSE) © 2026 Linkc-Chen (陈言)

Dayflow is independently licensed — see [JerryZLiu/Dayflow](https://github.com/JerryZLiu/Dayflow).

---

## Acknowledgements

- **[Dayflow](https://github.com/JerryZLiu/Dayflow)** by [@JerryZLiu](https://github.com/JerryZLiu) — the upstream that makes all this possible
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** by Anthropic — the skill execution runtime
- **[LinkcOS](https://github.com/noonsleeping/linkc-os)** — the personal wiki framework these skills were designed for
