#!/usr/bin/env python3
"""
trends.py — aggregate the journal index into gentle, factual trends.

Reads journal/index.jsonl (one JSON object per entry) and reports mood average,
current logging streak, recurring tags/themes and recent mood direction. These
are DESCRIPTIVE facts about what the user wrote — not predictions and not
judgments. "Streaks" are surfaced without guilt (a gap is just a gap).

Usage:
  python3 trends.py [--home ~/.companion] [--days 30]
  # or import: from trends import aggregate
"""
import argparse
import datetime
import json
import os
from collections import Counter


def _home(explicit=None):
    return explicit or os.environ.get("COMPANION_HOME") or os.path.expanduser("~/.companion")


def _load(index_path):
    rows = []
    if not os.path.exists(index_path):
        return rows
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _streak(dates):
    """Consecutive-day logging streak ending today or yesterday (guilt-free)."""
    if not dates:
        return 0
    days = sorted({datetime.date.fromisoformat(d) for d in dates}, reverse=True)
    today = datetime.date.today()
    if (today - days[0]).days > 1:
        return 0  # streak ended; not a failure, just information
    streak = 1
    for prev, cur in zip(days, days[1:]):
        if (prev - cur).days == 1:
            streak += 1
        else:
            break
    return streak


def aggregate(home=None, days=30):
    home = _home(home)
    index_path = os.path.join(home, "journal", "index.jsonl")
    rows = _load(index_path)
    if not rows:
        return {"entries": 0, "note": "No journal entries yet."}

    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    recent = [r for r in rows
              if r.get("date") and datetime.date.fromisoformat(r["date"]) >= cutoff]

    moods = [r["mood"] for r in recent if isinstance(r.get("mood"), (int, float))]
    tags = Counter(t for r in recent for t in (r.get("tags") or []))
    themes = Counter(t for r in recent for t in (r.get("themes") or []))
    people = Counter(p for r in recent for p in (r.get("people") or []))

    # mood direction: compare first vs second half of the window
    direction = None
    if len(moods) >= 4:
        half = len(moods) // 2
        first = sum(moods[:half]) / half
        second = sum(moods[half:]) / (len(moods) - half)
        delta = second - first
        direction = ("improving" if delta > 0.5 else
                     "declining" if delta < -0.5 else "steady")

    return {
        "window_days": days,
        "entries": len(rows),
        "entries_in_window": len(recent),
        "streak_days": _streak([r["date"] for r in rows if r.get("date")]),
        "mood_avg": round(sum(moods) / len(moods), 1) if moods else None,
        "mood_direction": direction,
        "crisis_flags_in_window": sum(1 for r in recent if r.get("crisis_flag")),
        "top_tags": tags.most_common(6),
        "top_themes": themes.most_common(6),
        "recurring_people": [p for p, c in people.most_common(6) if c > 1],
        "_note": "Descriptive summary of what was logged. Not a prediction.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    print(json.dumps(aggregate(args.home, args.days), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
