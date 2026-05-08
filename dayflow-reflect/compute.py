#!/usr/bin/env python3
"""dayflow-reflect compute.py v0.1

Read a dayflow daily/YYYY-MM-DD.md and compute hard metrics.
Output JSON to stdout for the SKILL.md driver to consume.

Usage:
    python3 compute.py /path/to/daily/2026-05-06.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
FOCUS_BLOCK_MIN_DURATION_MIN = 30
FOCUS_BLOCK_MAX_GAP_MIN = 5
HEAVY_SWITCH_THRESHOLD = 3  # >= N category switches in one hour
RECURRENCE_THRESHOLD = 3     # subcategory appears in N+ disjoint windows


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter (we don't have pyyaml). Returns (data, body)."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)

    data: dict = {}
    current_list_key: str | None = None
    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        # List continuation: "  - {...}" under a key
        if line.startswith("  - ") and current_list_key:
            item = line[4:].strip()
            data.setdefault(current_list_key, []).append(item)
            continue
        # Key: value or Key: (start of list)
        m2 = re.match(r"^([\w_]+):\s*(.*)$", line)
        if m2:
            key, val = m2.group(1), m2.group(2).strip()
            current_list_key = None
            if val == "" or val == "[]":
                data[key] = []
                current_list_key = key if val == "" else None
            else:
                data[key] = val
    return data, body


def parse_inline_dict(s: str) -> dict:
    """Parse `{start: "13:25", end: "15:37", duration_min: 132}` into a dict."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    out: dict = {}
    # Simple split on commas (values don't contain unescaped commas in our format)
    for pair in re.split(r",\s*", s):
        if ":" not in pair:
            continue
        k, v = pair.split(":", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # try int
        try:
            v_typed: int | str = int(v)
        except ValueError:
            v_typed = v
        out[k] = v_typed
    return out


CARD_HEADING_RE = re.compile(
    r"^### (\d{2}:\d{2})[–-](\d{2}:\d{2})\s+·\s+(.+?)(?:\s+\[([^\]]+)\])?$"
)
IDLE_HEADING_RE = re.compile(r"^### (\d{2}:\d{2})[–-](\d{2}:\d{2})\s+·\s+Idle(?:\s+\((\d+)\s*min\))?$")


def parse_timeline(body: str) -> list[dict]:
    """Extract cards from the AUTO body. Each card = {start, end, title, category, summary}."""
    # Only consider lines inside the AUTO block
    auto = re.search(
        r"<!--\s*DAYFLOW:AUTO:START\s*-->(.*?)<!--\s*DAYFLOW:AUTO:END\s*-->",
        body,
        re.DOTALL,
    )
    if auto:
        body = auto.group(1)

    cards: list[dict] = []
    current: dict | None = None
    for line in body.splitlines():
        idle_m = IDLE_HEADING_RE.match(line)
        if idle_m:
            if current:
                cards.append(current)
            current = {
                "start": idle_m.group(1),
                "end": idle_m.group(2),
                "title": "Idle",
                "category": "Idle",
                "summary": "",
            }
            continue
        m = CARD_HEADING_RE.match(line)
        if m:
            if current:
                cards.append(current)
            current = {
                "start": m.group(1),
                "end": m.group(2),
                "title": m.group(3).strip(),
                "category": (m.group(4) or "Unknown").strip(),
                "summary": "",
            }
            continue
        if current is not None and line and not line.startswith("- "):
            # Append to summary (skip distraction sub-bullets)
            current["summary"] += (line + " ")
    if current:
        cards.append(current)

    for c in cards:
        c["summary"] = c["summary"].strip()

    # Filter Dayflow analysis-failure noise (not real activity)
    cards = [c for c in cards if c["title"].strip() != "Processing failed"]
    return cards


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def card_duration_min(c: dict) -> int:
    return max(0, to_minutes(c["end"]) - to_minutes(c["start"]))


def compute_focus_blocks(cards: list[dict]) -> list[dict]:
    """Continuous ≥30 min same-category blocks, allowing ≤5 min gap between cards."""
    blocks: list[dict] = []
    if not cards:
        return blocks

    current = {
        "start": cards[0]["start"],
        "end": cards[0]["end"],
        "category": cards[0]["category"],
        "subcategories": [],  # we don't have subcategory in body; placeholder
        "card_titles": [cards[0]["title"]],
    }

    for c in cards[1:]:
        gap = to_minutes(c["start"]) - to_minutes(current["end"])
        same_cat = c["category"] == current["category"]
        if same_cat and gap <= FOCUS_BLOCK_MAX_GAP_MIN:
            current["end"] = c["end"]
            current["card_titles"].append(c["title"])
        else:
            duration = to_minutes(current["end"]) - to_minutes(current["start"])
            if duration >= FOCUS_BLOCK_MIN_DURATION_MIN:
                current["duration_min"] = duration
                blocks.append(current)
            current = {
                "start": c["start"],
                "end": c["end"],
                "category": c["category"],
                "subcategories": [],
                "card_titles": [c["title"]],
            }

    duration = to_minutes(current["end"]) - to_minutes(current["start"])
    if duration >= FOCUS_BLOCK_MIN_DURATION_MIN:
        current["duration_min"] = duration
        blocks.append(current)

    return blocks


def compute_switches_per_hour(cards: list[dict]) -> dict[int, int]:
    """For each hour, count category switches that occur within it."""
    hour_switches: defaultdict[int, int] = defaultdict(int)
    for i in range(1, len(cards)):
        if cards[i]["category"] != cards[i - 1]["category"]:
            switch_hour = int(cards[i]["start"].split(":")[0])
            hour_switches[switch_hour] += 1
    return dict(hour_switches)


def compute_title_recurrence(cards: list[dict]) -> dict:
    """Detect recurrence by title-keyword matching (since we don't have subcategory in body).
    Returns {keyword: [time_ranges]} for keywords appearing in ≥3 disjoint cards.
    Note: This is a fuzzy heuristic — Claude can refine in the SKILL flow.
    """
    # Group cards by normalized first 6-chars of title
    title_groups: defaultdict[str, list[dict]] = defaultdict(list)
    for c in cards:
        if c["category"] == "Idle":
            continue
        # Use first 6 Chinese chars or 12 ASCII chars as fuzzy key
        key = c["title"][:6]
        title_groups[key].append({"start": c["start"], "end": c["end"], "title": c["title"]})

    recurrence: dict = {}
    for key, items in title_groups.items():
        if len(items) >= RECURRENCE_THRESHOLD:
            recurrence[key] = items
    return recurrence


def main():
    if len(sys.argv) < 2:
        print("Usage: compute.py <daily-file.md>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"❌ File not found: {path}", file=sys.stderr)
        sys.exit(2)

    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    cards = parse_timeline(body)

    # Categories
    categories = Counter(c["category"] for c in cards)
    work_minutes = sum(card_duration_min(c) for c in cards if c["category"] == "Work")
    total_minutes = sum(card_duration_min(c) for c in cards)

    focus_blocks = compute_focus_blocks(cards)
    switches = compute_switches_per_hour(cards)
    heavy_switch_hours = {h: n for h, n in switches.items() if n >= HEAVY_SWITCH_THRESHOLD}
    recurrence = compute_title_recurrence(cards)

    # Weekday from frontmatter date
    date_str = (fm.get("date") or "").strip().strip('"')
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = WEEKDAY_CN[dt.weekday()]
    except (ValueError, TypeError):
        weekday = "?"

    # Large gaps from frontmatter (already parsed list of inline dicts as strings)
    raw_gaps = fm.get("large_gaps") or []
    if isinstance(raw_gaps, list):
        gaps = [parse_inline_dict(g) for g in raw_gaps if g]
    else:
        gaps = []

    out = {
        "date": date_str,
        "weekday": weekday,
        "card_count": len(cards),
        "categories_dayflow": dict(categories),  # raw Dayflow category counts (kept for reference; not used as final output)
        "total_minutes": total_minutes,
        "cards": [
            {
                "start": c["start"],
                "end": c["end"],
                "duration_min": card_duration_min(c),
                "title": c["title"],
                "summary": c["summary"][:300],  # truncate to keep JSON readable
                "dayflow_category": c["category"],
            }
            for c in cards
        ],
        "focus_minutes_dayflow": work_minutes,  # by Dayflow category=Work; Claude recomputes by 10-cat system
        "focus_blocks_dayflow": [
            {
                "start": b["start"],
                "end": b["end"],
                "category": b["category"],
                "duration_min": b["duration_min"],
                "card_titles": b["card_titles"],
            }
            for b in focus_blocks
        ],
        "long_blocks_2h": [b for b in [
            {
                "start": b["start"],
                "end": b["end"],
                "category": b["category"],
                "duration_min": b["duration_min"],
            }
            for b in focus_blocks
        ] if b["duration_min"] >= 120],
        "category_switches_per_hour": switches,
        "heavy_switch_hours": heavy_switch_hours,
        "title_recurrence": recurrence,
        "large_gaps": gaps,
        "frontmatter_passthrough": {
            "card_count": fm.get("card_count"),
            "total_distractions": fm.get("total_distractions"),
            "largest_gap_min": fm.get("largest_gap_min"),
            "time_coverage": (fm.get("time_coverage") or "").strip('"'),
            "status": fm.get("status"),
        },
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
