#!/usr/bin/env python3
"""dayflow-ingest v0.2

Extract Dayflow timeline_cards into LinkcOS 04-workflow/dayflow/daily/.
Pure stdlib (no pyyaml). Idempotent writes: AUTO marker block is replaced,
human notes outside the markers are preserved.

Status codes: complete / empty / failed (no judgment about gaps —
gap data is metadata in frontmatter, downstream skills decide context).

Self-heal: each run scans last 7 days, retries failed and missing-from-log dates.
Schema drift detection compares timeline_cards SQL vs snapshot; warns but does not block.

Usage:
    python3 ingest.py                  # extract yesterday + self-heal last 7 days
    python3 ingest.py 2026-05-06       # target a specific date + self-heal
    python3 ingest.py --all            # bulk-extract ALL distinct days from SQLite
                                       # (for one-time backfill / migration / disaster recovery;
                                       # skips self-heal and per-day wiki/log entries —
                                       # writes 1 summary line to wiki/log.md instead)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# === Paths & constants ===
HOME = Path.home()
DB_PATH = Path(os.environ.get(
    "DAYFLOW_DB_PATH",
    str(HOME / "Library" / "Application Support" / "Dayflow" / "chunks.sqlite"),
))
WIKI_ROOT = HOME / "linkc-os" / "04-workflow" / "dayflow"
DAILY_DIR = WIKI_ROOT / "daily"
META_DIR = WIKI_ROOT / "meta"
INGEST_LOG = META_DIR / "ingest-log.md"
SCHEMA_SNAPSHOT = META_DIR / "schema-snapshot.md"
WIKI_LOG = HOME / "linkc-os" / "02-wiki" / "log.md"

TZ = ZoneInfo("Asia/Shanghai")

AUTO_START = "<!-- DAYFLOW:AUTO:START -->"
AUTO_END = "<!-- DAYFLOW:AUTO:END -->"
HUMAN_HEADER = "## 人工注释"
HUMAN_HINT = "（陈言手写区，不会被覆盖）"

GAP_REPORT_THRESHOLD_S = 2 * 3600  # gaps >2h are recorded as metadata (not a status judgment)
SELF_HEAL_WINDOW_DAYS = 7

STATUS_ICON = {
    "complete": "✅ complete",
    "empty": "⏭️ empty",
    "failed": "❌ failed",
}
ICON_TO_STATUS = {v: k for k, v in STATUS_ICON.items()}

OVERVIEW_HEADER = "## 状态总览"
RUNS_HEADER = "## 运行流水"

FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
RETRIES_RE = re.compile(r"\(重试 (\d+) 次\)")
SNAPSHOT_TIMELINE_SQL_RE = re.compile(
    r"### timeline_cards\n\n```sql\n(.*?)\n```",
    re.DOTALL,
)


# === Time helpers ===

def now() -> datetime:
    return datetime.now(TZ)

def yesterday_iso() -> str:
    return (now() - timedelta(days=1)).date().isoformat()

def fmt_time(ts: int | None) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


# === SQL extraction ===

def extract_day(day: str) -> dict:
    """Returns {status, cards?, card_count, categories, total_distractions, time_coverage,
    largest_gap_min, large_gaps, error?}.

    status ∈ {complete, empty, failed} — no judgment about gaps.
    Gaps >2h are recorded as metadata (large_gaps) for downstream skills.
    """
    if not DB_PATH.exists():
        return {"status": "failed", "error": f"Dayflow database not found: {DB_PATH}"}

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, start, end, start_ts, end_ts, title, summary, "
            "category, subcategory, metadata "
            "FROM timeline_cards "
            "WHERE day = ? AND is_deleted = 0 "
            "ORDER BY start_ts ASC",
            (day,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        return {"status": "failed", "error": f"SQL error: {type(e).__name__}: {e}"}

    if not rows:
        return {
            "status": "empty",
            "cards": [],
            "card_count": 0,
            "categories": {},
            "total_distractions": 0,
            "time_coverage": "",
            "largest_gap_min": 0,
            "large_gaps": [],
        }

    categories = Counter(r["category"] or "Unknown" for r in rows)
    total_distractions = 0
    for r in rows:
        try:
            md = json.loads(r["metadata"]) if r["metadata"] else {}
        except json.JSONDecodeError:
            md = {}
        total_distractions += len(md.get("distractions") or [])

    first_ts = rows[0]["start_ts"]
    last_ts = rows[-1]["end_ts"]
    time_coverage = f"{fmt_time(first_ts)}–{fmt_time(last_ts)}" if first_ts and last_ts else ""

    # Gap metadata (no judgment — just record gaps >2h for downstream skills)
    large_gaps: list[dict] = []
    largest_gap_s = 0
    for i in range(len(rows) - 1):
        a_end = rows[i]["end_ts"]
        b_start = rows[i + 1]["start_ts"]
        if a_end and b_start:
            gap_s = b_start - a_end
            if gap_s > largest_gap_s:
                largest_gap_s = gap_s
            if gap_s > GAP_REPORT_THRESHOLD_S:
                large_gaps.append({
                    "start": fmt_time(a_end),
                    "end": fmt_time(b_start),
                    "duration_min": gap_s // 60,
                })

    return {
        "status": "complete",
        "cards": rows,
        "card_count": len(rows),
        "categories": dict(categories),
        "total_distractions": total_distractions,
        "time_coverage": time_coverage,
        "largest_gap_min": largest_gap_s // 60,
        "large_gaps": large_gaps,
    }


# === Rendering ===

def render_card(row: dict) -> str:
    start_ts = row.get("start_ts")
    end_ts = row.get("end_ts")
    time_range = f"{fmt_time(start_ts)}–{fmt_time(end_ts)}"

    category = (row.get("category") or "Unknown").strip()
    title = (row.get("title") or "").strip() or "(无标题)"

    if category == "Idle":
        if start_ts and end_ts:
            minutes = max(1, (end_ts - start_ts) // 60)
            return f"### {time_range} · Idle ({minutes} min)\n"
        return f"### {time_range} · Idle\n"

    lines = [f"### {time_range} · {title} [{category}]"]
    summary = (row.get("summary") or "").strip()
    if summary:
        lines.append(summary)

    try:
        md = json.loads(row["metadata"]) if row.get("metadata") else {}
    except json.JSONDecodeError:
        md = {}

    for d in (md.get("distractions") or []):
        d_start = (d.get("startTime") or "?").strip()
        d_end = (d.get("endTime") or "?").strip()
        d_title = (d.get("title") or "").strip()
        d_summary = (d.get("summary") or "").strip()
        if d_summary:
            lines.append(f"- ⚠️ {d_start}–{d_end} {d_title}：{d_summary}")
        else:
            lines.append(f"- ⚠️ {d_start}–{d_end} {d_title}")

    return "\n".join(lines) + "\n"


def render_frontmatter(day: str, payload: dict, ingested_at: datetime) -> str:
    cats = payload.get("categories") or {}
    cats_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
    coverage = payload.get("time_coverage") or ""
    largest_gap = int(payload.get("largest_gap_min", 0))
    large_gaps = payload.get("large_gaps") or []

    lines = [
        "---",
        f"date: {day}",
        "source: dayflow",
        f"ingested_at: {ingested_at.isoformat(timespec='seconds')}",
        f"card_count: {payload.get('card_count', 0)}",
        f'time_coverage: "{coverage}"',
        f"status: {payload['status']}",
        f"categories: {{{cats_str}}}",
        f"total_distractions: {payload.get('total_distractions', 0)}",
        f"largest_gap_min: {largest_gap}",
    ]
    if large_gaps:
        lines.append("large_gaps:")
        for g in large_gaps:
            lines.append(f'  - {{start: "{g["start"]}", end: "{g["end"]}", duration_min: {g["duration_min"]}}}')
    else:
        lines.append("large_gaps: []")
    lines.append("---")
    return "\n".join(lines)


def render_auto_body(payload: dict) -> str:
    if payload["status"] == "empty":
        return "## Timeline\n\n_当天 Dayflow 无数据。_\n"

    lines = ["## Timeline", ""]
    for row in payload.get("cards") or []:
        lines.append(render_card(row))
    return "\n".join(lines)


def render_full(day: str, payload: dict, ingested_at: datetime) -> str:
    fm = render_frontmatter(day, payload, ingested_at)
    auto_body = render_auto_body(payload)
    return "\n".join([
        fm,
        "",
        f"# {day} 桌面活动 (Dayflow)",
        "",
        "> 此文件由 dayflow-ingest 自动生成。",
        "> AUTO 标记区由下次运行覆盖；标记外（人工注释、链接、标签）保留。",
        "",
        AUTO_START,
        "",
        auto_body,
        AUTO_END,
        "",
        HUMAN_HEADER,
        "",
        HUMAN_HINT,
        "",
    ])


# === Idempotent write ===

def write_idempotent(day: str, payload: dict, ingested_at: datetime) -> tuple[bool, str | None]:
    """Returns (created: bool, warning: str | None)."""
    target = DAILY_DIR / f"{day}.md"
    fm = render_frontmatter(day, payload, ingested_at)
    auto_body = render_auto_body(payload)
    auto_block = f"{AUTO_START}\n\n{auto_body}\n{AUTO_END}"

    if not target.exists():
        target.write_text(render_full(day, payload, ingested_at), encoding="utf-8")
        return True, None

    existing = target.read_text(encoding="utf-8")
    warnings: list[str] = []

    if FRONTMATTER_RE.match(existing):
        existing = FRONTMATTER_RE.sub(fm + "\n", existing, count=1)
    else:
        warnings.append("frontmatter 缺失，已在文件开头插入")
        existing = fm + "\n\n" + existing

    auto_pattern = re.compile(
        r"^" + re.escape(AUTO_START) + r".*?^" + re.escape(AUTO_END),
        re.DOTALL | re.MULTILINE,
    )
    if auto_pattern.search(existing):
        existing = auto_pattern.sub(auto_block, existing, count=1)
    else:
        warnings.append("AUTO 标记缺失或被破坏，已在文件末尾追加新 AUTO 区")
        existing = existing.rstrip() + "\n\n" + auto_block + "\n"

    target.write_text(existing, encoding="utf-8")
    return False, "; ".join(warnings) if warnings else None


# === Schema snapshot + drift ===

def write_schema_snapshot_if_missing() -> bool:
    if SCHEMA_SNAPSHOT.exists() or not DB_PATH.exists():
        return False

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return False

    lines = [
        "# Dayflow SQLite Schema Snapshot",
        "",
        f"> 首次抓取于 {now().isoformat(timespec='seconds')}。",
        "> 每次运行会比对 timeline_cards 的 SQL 是否变化，不一致时在 ingest-log 告警。",
        "",
    ]
    current_type = None
    for type_, name, sql in rows:
        if type_ != current_type:
            lines.append(f"## {type_}")
            lines.append("")
            current_type = type_
        lines.append(f"### {name}")
        lines.append("")
        if sql:
            lines.append("```sql")
            lines.append(sql.strip())
            lines.append("```")
            lines.append("")

    SCHEMA_SNAPSHOT.write_text("\n".join(lines), encoding="utf-8")
    return True


def detect_schema_drift() -> str | None:
    """Compare current timeline_cards SQL with snapshot. Returns description if drift, else None."""
    if not SCHEMA_SNAPSHOT.exists() or not DB_PATH.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='timeline_cards'"
        )
        row = cur.fetchone()
        conn.close()
        if not row or not row[0]:
            return "timeline_cards 表不存在"
        current_sql = row[0].strip()
    except Exception:
        return None

    snapshot_text = SCHEMA_SNAPSHOT.read_text(encoding="utf-8")
    m = SNAPSHOT_TIMELINE_SQL_RE.search(snapshot_text)
    if not m:
        return None  # Snapshot doesn't have timeline_cards SQL — skip check
    snapshot_sql = m.group(1).strip()

    if current_sql != snapshot_sql:
        return "timeline_cards schema 与 snapshot 不一致（可能 Dayflow 升级了表结构）"
    return None


# === ingest-log parsing & writing ===

def parse_existing_log() -> tuple[dict, list[str]]:
    """Returns (overview_rows, runs)."""
    overview_rows: dict[str, dict] = {}
    runs: list[str] = []
    if not INGEST_LOG.exists():
        return overview_rows, runs

    existing = INGEST_LOG.read_text(encoding="utf-8")

    ov_match = re.search(
        rf"{re.escape(OVERVIEW_HEADER)}\n\n.*?(?=\n{re.escape(RUNS_HEADER)}|\Z)",
        existing,
        re.DOTALL,
    )
    if ov_match:
        for line in ov_match.group(0).splitlines():
            if line.startswith("|") and not line.startswith("| 日期") and not line.startswith("|---"):
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) >= 6 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
                    overview_rows[parts[0]] = {
                        "date": parts[0],
                        "status": parts[1],
                        "card_count": parts[2],
                        "time_coverage": parts[3],
                        "last_update": parts[4],
                        "note": parts[5],
                    }

    runs_match = re.search(rf"{re.escape(RUNS_HEADER)}\n\n(.*?)$", existing, re.DOTALL)
    if runs_match:
        for line in runs_match.group(1).splitlines():
            if line.strip().startswith("- "):
                runs.append(line.rstrip())

    return overview_rows, runs


def parse_retries(note: str) -> int:
    m = RETRIES_RE.search(note or "")
    return int(m.group(1)) if m else 0


def fmt_note(payload: dict, retries: int) -> str:
    if payload["status"] == "failed":
        base = (payload.get("error") or "")[:60]
        if retries > 0:
            return f"{base} (重试 {retries} 次)"
        return base
    if payload["status"] == "empty":
        return "Dayflow 当日无数据"
    # complete: surface large-gap info as metadata, not a problem flag
    largest = int(payload.get("largest_gap_min", 0))
    n_gaps = len(payload.get("large_gaps") or [])
    if n_gaps > 0:
        return f"含 {n_gaps} 段 >2h 空白（最长 {largest} 分钟）"
    return "-"


def write_ingest_log(overview_rows: dict, runs: list[str]):
    out: list[str] = [
        "# Dayflow Ingest Log",
        "",
        "> 状态总览每次运行整表重写（按日期倒序）。运行流水只追加，不修改历史。",
        "",
        OVERVIEW_HEADER,
        "",
        "| 日期 | 状态 | 卡片数 | 时间覆盖 | 最后更新 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for date_key in sorted(overview_rows.keys(), reverse=True):
        r = overview_rows[date_key]
        out.append(
            f"| {r['date']} | {r['status']} | {r['card_count']} | {r['time_coverage']} | {r['last_update']} | {r['note']} |"
        )
    out.append("")
    out.append(RUNS_HEADER)
    out.append("")
    out.extend(runs)
    out.append("")
    INGEST_LOG.write_text("\n".join(out), encoding="utf-8")


# === Self-heal candidate selection ===

def self_heal_candidates(target_day: str, overview: dict, days: int = SELF_HEAL_WINDOW_DAYS) -> list[str]:
    """Return last `days` dates (excluding target_day) that need retry: failed / not in log.

    Note: 'partial' is no longer a status — gap data lives in frontmatter as metadata.
    self-heal only retries genuine failures and missing-from-log dates.
    """
    today = now().date()
    candidates: list[str] = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        if d == target_day:
            continue
        row = overview.get(d)
        if row is None:
            candidates.append(d)
            continue
        raw_status = ICON_TO_STATUS.get(row["status"])
        if raw_status == "failed":
            candidates.append(d)
    candidates.sort()
    return candidates


# === wiki/log.md ===

def append_wiki_log(target_day: str, results: list[dict], ingested_at: datetime, schema_drift: str | None):
    if not WIKI_LOG.exists():
        return

    target = next((r for r in results if r["day"] == target_day), None)
    heals = [r for r in results if r["run_type"] == "self-heal"]

    timestamp = ingested_at.strftime("%Y-%m-%d %H:%M")

    if target:
        p = target["payload"]
        if p["status"] == "complete":
            head = f"dayflow {target_day} | {p['card_count']} cards | OK"
        elif p["status"] == "empty":
            head = f"dayflow {target_day} | empty"
        else:
            err = (p.get("error") or "")[:50]
            head = f"dayflow {target_day} | failed: {err}"
    else:
        head = f"dayflow {target_day}"

    if heals:
        changed = []
        for r in heals:
            if r["old_status"] != r["new_status"]:
                old = r["old_status"] or "missing"
                changed.append(f"{r['day']} {old}→{r['new_status']}")
        if changed:
            heal_str = f" (+heal: {len(heals)} touched, {', '.join(changed)})"
        else:
            heal_str = f" (+heal: {len(heals)} rechecked, no change)"
    else:
        heal_str = ""

    drift_str = " ⚠️ schema drift" if schema_drift else ""

    line = f"\n## [{timestamp}] ingest | {head}{heal_str}{drift_str}\n"
    with WIKI_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


# === Reporting ===

PREFIX = {"complete": "✅", "empty": "⏭️", "failed": "❌"}


def report(results: list[dict], schema_first: bool, schema_drift: str | None):
    for r in results:
        day = r["day"]
        p = r["payload"]
        prefix = PREFIX.get(p["status"], "?")
        tag = f"[{r['run_type']}] " if r["run_type"] != "manual" else ""
        if p["status"] == "complete":
            cats = p.get("categories", {})
            cats_str = " · ".join(f"{k} {v}" for k, v in sorted(cats.items())) or "-"
            n_gaps = len(p.get("large_gaps") or [])
            largest = p.get("largest_gap_min", 0)
            gap_str = f" · 大空白 {n_gaps} 段（最长 {largest} 分钟）" if n_gaps else ""
            print(f"{prefix} {tag}dayflow {day}: {p['card_count']} cards ({p.get('time_coverage', '')})")
            print(f"   分类: {cats_str} · 打断 {p.get('total_distractions', 0)}{gap_str}")
        elif p["status"] == "empty":
            print(f"{prefix} {tag}dayflow {day}: empty")
        else:
            print(f"{prefix} {tag}dayflow {day}: failed - {p.get('error', '')}")
        if r.get("warning"):
            print(f"   ⚠️ 警告: {r['warning']}")

    if schema_first:
        print(f"📐 已写入首次 schema snapshot: {SCHEMA_SNAPSHOT}")
    if schema_drift:
        print(f"⚠️ schema drift: {schema_drift}")


# === Bulk backfill ===

def bulk_extract_all():
    """One-time backfill: extract every distinct day from SQLite.

    Skips self-heal (already covering everything) and per-day wiki/log entries.
    Writes one summary line to wiki/log.md and one bulk run line to ingest-log.
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        print(f"❌ DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(2)

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT DISTINCT day FROM timeline_cards WHERE is_deleted=0 ORDER BY day ASC"
        )
        days = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"❌ SQL error: {e}", file=sys.stderr)
        sys.exit(2)

    if not days:
        print("⏭️ Dayflow database has no timeline_cards data.")
        sys.exit(0)

    print(f"📦 bulk extract {len(days)} days: {days[0]} → {days[-1]}")
    print()

    write_schema_snapshot_if_missing()
    overview_rows, runs = parse_existing_log()
    ingested_at = now()

    n_complete = 0
    n_with_gaps = 0
    n_failed = 0
    for i, day in enumerate(days, 1):
        payload = extract_day(day)

        warning = None
        if payload["status"] in ("complete", "empty"):
            _, warning = write_idempotent(day, payload, ingested_at)

        cards_n = str(payload.get("card_count", 0)) if payload["status"] != "failed" else "-"
        coverage = payload.get("time_coverage", "") if payload["status"] == "complete" else "-"
        overview_rows[day] = {
            "date": day,
            "status": STATUS_ICON.get(payload["status"], payload["status"]),
            "card_count": cards_n,
            "time_coverage": coverage or "-",
            "last_update": ingested_at.strftime("%Y-%m-%d %H:%M"),
            "note": fmt_note(payload, 0),
        }

        n_gaps = len(payload.get("large_gaps") or [])
        if payload["status"] == "complete":
            n_complete += 1
            if n_gaps:
                n_with_gaps += 1
        elif payload["status"] == "failed":
            n_failed += 1

        gap_str = f" · {n_gaps} large gap{'s' if n_gaps != 1 else ''}" if n_gaps else ""
        progress = f"[{i:>3}/{len(days)}]"
        print(f"  {progress} {day}: {payload.get('card_count', 0):>3} cards{gap_str}")

    # One bulk run line in ingest-log
    runs.append(
        f"- {ingested_at.strftime('%Y-%m-%d %H:%M:%S')} | bulk | extract all {len(days)} days | "
        f"{days[0]}..{days[-1]} | {n_complete} complete ({n_with_gaps} w/ gaps), {n_failed} failed"
    )
    write_ingest_log(overview_rows, runs)

    # One summary line in wiki/log.md
    if WIKI_LOG.exists():
        timestamp = ingested_at.strftime("%Y-%m-%d %H:%M")
        line = (
            f"\n## [{timestamp}] ingest | dayflow bulk backfill | "
            f"{len(days)} days ({days[0]}..{days[-1]}) | "
            f"{n_complete} complete ({n_with_gaps} w/ >2h gaps){f', {n_failed} failed' if n_failed else ''}\n"
        )
        with WIKI_LOG.open("a", encoding="utf-8") as f:
            f.write(line)

    print()
    print(f"✅ bulk done: {n_complete} complete ({n_with_gaps} 含大空白) · {n_failed} failed")
    print(f"   范围: {days[0]} → {days[-1]}")
    print(f"   ingest-log + wiki/log.md 各加 1 行汇总")
    sys.exit(0 if n_failed == 0 else 2)


# === Main ===

def main():
    # Bulk mode: one-shot extract every day in DB
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        bulk_extract_all()
        return

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        target_day = sys.argv[1]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", target_day):
            print(f"❌ 日期格式错误: {target_day}", file=sys.stderr)
            sys.exit(1)
    else:
        target_day = yesterday_iso()

    ingested_at = now()
    schema_first = write_schema_snapshot_if_missing()
    schema_drift = detect_schema_drift()

    overview_rows, runs = parse_existing_log()

    # Build queue: target first (manual), then self-heal candidates
    queue: list[tuple[str, str]] = [(target_day, "manual")]
    heal_dates = self_heal_candidates(target_day, overview_rows)
    for d in heal_dates:
        queue.append((d, "self-heal"))

    results: list[dict] = []
    for day, run_type in queue:
        old_row = overview_rows.get(day)
        old_retries = parse_retries(old_row["note"]) if old_row else 0
        old_status = ICON_TO_STATUS.get(old_row["status"]) if old_row else None

        payload = extract_day(day)

        # Retry counter: increment on failed self-heal; reset on success
        if payload["status"] == "failed" and run_type == "self-heal":
            retries = old_retries + 1
        elif payload["status"] in ("complete", "empty"):
            retries = 0
        else:
            retries = old_retries

        # Write daily file (skip on failed)
        warning = None
        if payload["status"] in ("complete", "empty"):
            _created, warning = write_idempotent(day, payload, ingested_at)

        # Update overview
        cards_n = str(payload.get("card_count", 0)) if payload["status"] != "failed" else "-"
        coverage = payload.get("time_coverage", "") if payload["status"] == "complete" else "-"
        overview_rows[day] = {
            "date": day,
            "status": STATUS_ICON.get(payload["status"], payload["status"]),
            "card_count": cards_n,
            "time_coverage": coverage or "-",
            "last_update": ingested_at.strftime("%Y-%m-%d %H:%M"),
            "note": fmt_note(payload, retries),
        }

        # Build run line
        if payload["status"] == "complete":
            n_gaps = len(payload.get("large_gaps") or [])
            gap_note = f" ({n_gaps} large gaps)" if n_gaps else ""
            outcome = f"{payload['card_count']} cards | OK{gap_note}"
        elif payload["status"] == "empty":
            outcome = "empty"
        else:
            outcome = f"failed: {(payload.get('error') or '')[:60]}"
        runs.append(
            f"- {ingested_at.strftime('%Y-%m-%d %H:%M:%S')} | {run_type} | extract {day} | {outcome}"
        )

        results.append({
            "day": day,
            "run_type": run_type,
            "payload": payload,
            "warning": warning,
            "old_status": old_status,
            "new_status": payload["status"],
        })

    if schema_drift:
        runs.append(
            f"- {ingested_at.strftime('%Y-%m-%d %H:%M:%S')} | warning | schema drift: {schema_drift}"
        )

    write_ingest_log(overview_rows, runs)
    append_wiki_log(target_day, results, ingested_at, schema_drift)
    report(results, schema_first, schema_drift)

    target_result = next((r for r in results if r["day"] == target_day), None)
    sys.exit(0 if (target_result and target_result["payload"]["status"] != "failed") else 2)


if __name__ == "__main__":
    main()
