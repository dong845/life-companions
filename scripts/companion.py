#!/usr/bin/env python3
"""
companion.py — the private-data dispatcher for the life-companion skill.

Owns everything under COMPANION_HOME (default ~/.companion, chmod 700): the
profile, the consent ledger, and the journal (a human-readable monthly .md plus a
machine-readable index.jsonl written atomically). All destructive operations are
first-class and actually delete data — "right to forget" is a feature, not a
disclaimer.

The model NEVER hand-edits these files; it calls this script so writes stay
atomic, consent-gated and auditable.

Subcommands:
  init                      create the private home (idempotent)
  status                    onboarding state, consent, journal counts
  read-profile [--json]     dump profile.yaml for the model to load
  set-profile --merge-json  deep-merge a JSON patch into profile.yaml
  consent --set k=yes|no    record consent per category (birth/relationships/mood)
  add-entry --text ...      append a journal entry (prose + atomic index line)
  trend [--days N]          descriptive journal trends (delegates to trends.py)
  search [--tag --text --since --until]
  forget --birth | --month YYYY-MM | --all --yes
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        return __import__(import_name)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", pkg], check=True)
        return __import__(import_name)


yaml = _ensure("PyYAML", "yaml")
from safety_scan import scan_text  # noqa: E402
import trends as trends_mod        # noqa: E402


# ---------------------------------------------------------------------------
def home_dir(explicit=None):
    return os.path.abspath(
        explicit or os.environ.get("COMPANION_HOME") or os.path.expanduser("~/.companion")
    )


def _paths(home):
    return {
        "home": home,
        "profile": os.path.join(home, "profile.yaml"),
        "consent": os.path.join(home, "consent.yaml"),
        "journal": os.path.join(home, "journal"),
        "index": os.path.join(home, "journal", "index.jsonl"),
        "state": os.path.join(home, "state"),
        "continuity": os.path.join(home, "state", "continuity.yaml"),
        "modules": os.path.join(home, "state", "modules"),
        "readme": os.path.join(home, "README.txt"),
    }


def _today():
    return datetime.date.today().isoformat()


def _atomic_write(path, text):
    """Write via temp file + os.replace so a crash never leaves a half file."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _load_yaml(path, default=None):
    if not os.path.exists(path):
        return {} if default is None else default
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path, data):
    _atomic_write(path, yaml.dump(data, allow_unicode=True, sort_keys=False))


def _deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        elif isinstance(v, list) and isinstance(base.get(k), list):
            # Append-UNION, not replace: preserves accumulated history (relationship
            # incidents, continuity threads) instead of silently dropping it. Items
            # already present are skipped, so re-sending the full list is a safe no-op
            # and sending just the new item appends it.
            base[k] = base[k] + [x for x in v if x not in base[k]]
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
def cmd_init(args):
    home = home_dir(args.home)
    p = _paths(home)
    os.makedirs(p["journal"], exist_ok=True)
    os.makedirs(p["modules"], exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass

    if not os.path.exists(p["profile"]):
        _save_yaml(p["profile"], {
            "schema_version": 1,
            "onboarding_complete": False,
            "created": _today(),
            "updated": _today(),
            "identity": {"name": None, "pronouns": None, "timezone": None, "locale": None},
            "birth": {"date": None, "time": None, "time_known": None, "place": None,
                      "lat": None, "lon": None, "tz_at_birth": None,
                      "conventions": {"true_solar_time": False, "zishi_rule": "late"}},
            "preferences": {"tone": "warm-direct", "advice_style": "options",
                            "skepticism": "high", "checkin_cadence": None},
            "context": {},
            "modules_enabled": ["journal"],
        })
    if not os.path.exists(p["consent"]):
        _save_yaml(p["consent"], {
            "birth": {"granted": None, "date": None},
            "relationships": {"granted": None, "date": None},
            "mood": {"granted": None, "date": None},
        })
    if not os.path.exists(p["continuity"]):
        _save_yaml(p["continuity"], {"rolling_summary": "", "open_threads": [],
                                     "recent_moods": [], "updated": _today()})
    if not os.path.exists(p["readme"]):
        _atomic_write(p["readme"],
            "This folder holds your private life-companion data.\n"
            f"Location: {home}\n\n"
            "• profile.yaml   — who you are + preferences (plain text, editable)\n"
            "• consent.yaml   — what you've allowed to be stored\n"
            "• journal/       — your entries (monthly .md + index.jsonl)\n"
            "• state/         — cached charts & continuity\n\n"
            "It never leaves your machine. To delete things, ask the companion to\n"
            '"forget my birth data" / "forget <month>" / "wipe everything", or just\n'
            "delete files here yourself.\n")
    print(json.dumps({"ok": True, "home": home,
                      "onboarding_complete": _load_yaml(p["profile"]).get("onboarding_complete")},
                     ensure_ascii=False))


def cmd_status(args):
    home = home_dir(args.home)
    p = _paths(home)
    if not os.path.exists(p["profile"]):
        print(json.dumps({"initialized": False, "home": home}, ensure_ascii=False))
        return
    prof = _load_yaml(p["profile"])
    consent = _load_yaml(p["consent"])
    rows = trends_mod._load(p["index"])
    last = rows[-1]["date"] if rows and rows[-1].get("date") else None
    print(json.dumps({
        "initialized": True,
        "home": home,
        "onboarding_complete": prof.get("onboarding_complete"),
        "name": prof.get("identity", {}).get("name"),
        "locale": prof.get("identity", {}).get("locale"),
        "modules_enabled": prof.get("modules_enabled"),
        "consent": {k: v.get("granted") for k, v in consent.items()},
        "journal_entries": len(rows),
        "last_entry": last,
    }, ensure_ascii=False, indent=2))


def cmd_read_profile(args):
    home = home_dir(args.home)
    prof = _load_yaml(_paths(home)["profile"])
    if args.json:
        print(json.dumps(prof, ensure_ascii=False, indent=2))
    else:
        print(yaml.dump(prof, allow_unicode=True, sort_keys=False))


def cmd_set_profile(args):
    home = home_dir(args.home)
    p = _paths(home)
    prof = _load_yaml(p["profile"])
    patch = json.loads(args.merge_json)
    _deep_merge(prof, patch)
    prof["updated"] = _today()
    _save_yaml(p["profile"], prof)
    print(json.dumps({"ok": True, "updated_keys": list(patch.keys())}, ensure_ascii=False))


def cmd_consent(args):
    home = home_dir(args.home)
    p = _paths(home)
    consent = _load_yaml(p["consent"])
    for pair in args.set:
        k, _, v = pair.partition("=")
        granted = v.strip().lower() in ("yes", "true", "1", "y")
        consent[k.strip()] = {"granted": granted, "date": _today()}
    _save_yaml(p["consent"], consent)
    print(json.dumps({"ok": True, "consent": {k: val.get("granted")
                      for k, val in consent.items()}}, ensure_ascii=False))


def cmd_continuity(args):
    """Read, merge into, or replace keys of state/continuity.yaml (the working memory).

    --merge-json  deep-merges (lists append-UNION) — use to ACCRETE: add a thread,
                  append a mood. It cannot edit or remove an existing list item.
    --replace-json OVERWRITES each given top-level key outright — use to CORRECT or
                  PRUNE: fix a stale open_thread, drop a resolved one, rewrite the
                  rolling_summary. Send the full intended value of each key you touch.
    Both bump `updated`. Replace wins if somehow both are given for a key."""
    home = home_dir(args.home)
    p = _paths(home)
    cont = _load_yaml(p["continuity"], {})
    touched = []
    if args.merge_json:
        patch = json.loads(args.merge_json)
        _deep_merge(cont, patch)
        touched += list(patch.keys())
    if getattr(args, "replace_json", None):
        rep = json.loads(args.replace_json)
        cont.update(rep)                       # overwrite top-level keys wholesale
        touched += list(rep.keys())
    if touched:
        cont["updated"] = _today()
        _save_yaml(p["continuity"], cont)
        print(json.dumps({"ok": True, "updated_keys": touched}, ensure_ascii=False))
    else:
        print(yaml.dump(cont, allow_unicode=True, sort_keys=False))


def cmd_followups(args):
    """Surface open action-threads DUE for a gentle nudge — turns continuity from
    passive memory into follow-through. A thread is due if it isn't closed and hasn't
    been nudged in --days (default 5). Read-only; the model does the warm follow-up,
    then records it by updating the thread's last_nudged/status via `continuity`.
    Thread shape: {thread, action, opened, last_nudged, status: open|in_progress|done}."""
    home = home_dir(args.home)
    cont = _load_yaml(_paths(home)["continuity"], {})
    today = datetime.date.today()

    def _age(d):
        try:
            return (today - datetime.date.fromisoformat(str(d))).days
        except (ValueError, TypeError):
            return None

    due = []
    for t in (cont.get("open_threads") or []):
        if not isinstance(t, dict):
            continue
        if (t.get("status") or "open").lower() in ("done", "closed", "resolved", "dropped"):
            continue
        since = _age(t.get("last_nudged")) if t.get("last_nudged") else None
        if since is None or since >= args.days:
            due.append({"thread": t.get("thread"), "action": t.get("action"),
                        "status": t.get("status") or "open", "opened": t.get("opened"),
                        "days_open": _age(t.get("opened")), "days_since_nudge": since})
    print(json.dumps({"due": due, "count": len(due),
                      "_note": "Gently follow up on these (nudge, don't nag); after "
                               "following up, set last_nudged=today (or status=done) via "
                               "`continuity --replace-json` with the full edited open_threads "
                               "list (NOT --merge-json, which would duplicate the thread)."},
                     ensure_ascii=False, indent=2))


def cmd_cache(args):
    """Read or merge a module's private cache at state/modules/<module>.yaml — e.g.
    relationships per-person tracking, or a cached natal chart. Modules read
    profile+continuity but write ONLY their own cache (prevents cross-module clobber)."""
    home = home_dir(args.home)
    p = _paths(home)
    os.makedirs(p["modules"], exist_ok=True)
    path = os.path.join(p["modules"], f"{args.module}.yaml")
    data = _load_yaml(path, {})
    if args.merge_json:
        _deep_merge(data, json.loads(args.merge_json))
        data["updated"] = _today()
        _save_yaml(path, data)
        print(json.dumps({"ok": True, "module": args.module}, ensure_ascii=False))
    else:
        print(yaml.dump(data, allow_unicode=True, sort_keys=False))


def cmd_add_entry(args):
    home = home_dir(args.home)
    p = _paths(home)
    consent = _load_yaml(p["consent"])
    date = args.date or _today()
    try:
        dt = datetime.date.fromisoformat(date)
    except ValueError:
        print(json.dumps({"ok": False, "error": f"bad --date '{date}', expect YYYY-MM-DD"},
                         ensure_ascii=False))
        return
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    themes = [t.strip() for t in (args.themes or "").split(",") if t.strip()]
    people = [t.strip() for t in (args.people or "").split(",") if t.strip()]

    # mood is gated by consent.mood — fail CLOSED: store only when explicitly granted
    # (at init `granted` is None = never asked, so mood must NOT be stored yet).
    mood = args.mood
    if mood is not None and consent.get("mood", {}).get("granted") is not True:
        mood = None

    scan = scan_text(args.text)
    # the model is the real crisis detector; --crisis lets it force the flag when it
    # sees something the keyword backstop misses (see safety.md).
    crisis_flag = scan["crisis_flag"] or bool(args.crisis)

    # 1) append human-readable prose (monthly file)
    month_path = os.path.join(p["journal"], f"{dt.year:04d}-{dt.month:02d}.md")
    dow = dt.strftime("%a")
    header = f"## {date}  ({dow})"
    meta_bits = []
    if mood is not None:
        meta_bits.append(f"mood {mood}/10")
    if args.energy:
        meta_bits.append(f"energy: {args.energy}")
    if meta_bits:
        header += " · " + " · ".join(meta_bits)
    block = [header]
    if tags:
        block.append(f"tags: {', '.join(tags)}")
    block.append("")
    block.append(args.text.strip())
    if args.reflection:
        block.append("")
        block.append(f"> companion: {args.reflection.strip()}")
    block.append("")
    existing = ""
    if os.path.exists(month_path):
        with open(month_path, encoding="utf-8") as f:
            existing = f.read()
    offset = len(existing)
    _atomic_write(month_path, existing + ("\n" if existing and not existing.endswith("\n") else "") + "\n".join(block) + "\n")

    # 2) append index line atomically (read-all, rewrite temp, replace)
    row = {
        "date": date, "mood": mood, "energy": args.energy,
        "tags": tags, "themes": themes,
        "module_touch": [args.module] if args.module else [],
        "people": people, "crisis_flag": crisis_flag,
        "file": os.path.relpath(month_path, home), "offset": offset,
    }
    existing_index = ""
    if os.path.exists(p["index"]):
        with open(p["index"], encoding="utf-8") as f:
            existing_index = f.read()
    _atomic_write(p["index"], existing_index + json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps({"ok": True, "date": date, "file": row["file"],
                      "crisis_flag": crisis_flag, "crisis_forced": bool(args.crisis),
                      "safety_scan": scan}, ensure_ascii=False, indent=2))


def cmd_trend(args):
    print(json.dumps(trends_mod.aggregate(home_dir(args.home), args.days),
                     ensure_ascii=False, indent=2))


def cmd_journal(args):
    """Print the human-readable journal prose (most recent first), optionally since a
    date — so the user can actually re-read their entries, not just aggregates."""
    home = home_dir(args.home)
    rows = trends_mod._load(_paths(home)["index"])
    if args.since:
        rows = [r for r in rows if r.get("date", "") >= args.since]
    if args.tag:
        rows = [r for r in rows if args.tag in (r.get("tags") or [])]
    rows = rows[-args.limit:] if args.limit else rows
    blocks = []
    for r in rows:
        fp = os.path.join(home, r.get("file", ""))
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as f:
            content = f.read()
        block = content[r.get("offset", 0):]
        nxt = block.find("\n## ", 1)
        if nxt != -1:
            block = block[:nxt]
        blocks.append(block.strip())
    print("\n\n".join(reversed(blocks)) if blocks else "(no matching journal entries)")


def cmd_search(args):
    home = home_dir(args.home)
    rows = trends_mod._load(_paths(home)["index"])
    out = []
    for r in rows:
        if args.tag and args.tag not in (r.get("tags") or []):
            continue
        if args.since and r.get("date", "") < args.since:
            continue
        if args.until and r.get("date", "") > args.until:
            continue
        out.append(r)
    # optional free-text match against the prose — scoped to the ENTRY's own block
    # (all entries in a month share one file, so match on the block from this entry's
    # offset up to the next entry's "## " header, not the whole file).
    if args.text:
        needle = args.text.lower()
        kept = []
        for r in out:
            fp = os.path.join(home, r.get("file", ""))
            if not os.path.exists(fp):
                continue
            with open(fp, encoding="utf-8") as f:
                content = f.read()
            block = content[r.get("offset", 0):]
            nxt = block.find("\n## ", 1)          # start of the next entry, if any
            if nxt != -1:
                block = block[:nxt]
            if needle in block.lower():
                kept.append(r)
        out = kept
    print(json.dumps({"matches": len(out), "entries": out}, ensure_ascii=False, indent=2))


def cmd_forget(args):
    home = home_dir(args.home)
    p = _paths(home)
    done = []
    if args.all:
        if not args.yes:
            print(json.dumps({"ok": False, "error": "refusing to wipe without --yes"}))
            return
        import shutil
        if os.path.exists(home):
            shutil.rmtree(home)
        print(json.dumps({"ok": True, "wiped": home}, ensure_ascii=False))
        return
    if args.birth:
        prof = _load_yaml(p["profile"])
        prof["birth"] = {"date": None, "time": None, "time_known": None, "place": None,
                         "lat": None, "lon": None, "tz_at_birth": None,
                         "conventions": prof.get("birth", {}).get("conventions", {})}
        prof["updated"] = _today()
        _save_yaml(p["profile"], prof)
        consent = _load_yaml(p["consent"])
        consent["birth"] = {"granted": False, "date": _today()}
        _save_yaml(p["consent"], consent)
        # delete cached natal chart
        destiny = os.path.join(p["modules"], "destiny.yaml")
        if os.path.exists(destiny):
            os.remove(destiny)
        done.append("birth data + cached charts removed")
    if args.month:
        month_path = os.path.join(p["journal"], f"{args.month}.md")
        if os.path.exists(month_path):
            os.remove(month_path)
        # drop index rows for that month
        rows = trends_mod._load(p["index"])
        kept = [r for r in rows if not (r.get("date", "").startswith(args.month))]
        _atomic_write(p["index"], "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
        done.append(f"journal {args.month} removed ({len(rows) - len(kept)} entries)")
    print(json.dumps({"ok": True, "done": done or ["nothing matched"]}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="life-companion private-data dispatcher")
    ap.add_argument("--home", default=None, help="override COMPANION_HOME")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("status").set_defaults(func=cmd_status)

    rp = sub.add_parser("read-profile"); rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_read_profile)

    sp = sub.add_parser("set-profile"); sp.add_argument("--merge-json", required=True)
    sp.set_defaults(func=cmd_set_profile)

    cs = sub.add_parser("consent"); cs.add_argument("--set", nargs="+", required=True,
        help="category=yes|no, e.g. birth=yes mood=no")
    cs.set_defaults(func=cmd_consent)

    ae = sub.add_parser("add-entry")
    ae.add_argument("--text", required=True)
    ae.add_argument("--date", default=None)
    ae.add_argument("--mood", type=int, default=None)
    ae.add_argument("--energy", default=None)
    ae.add_argument("--tags", default=None)
    ae.add_argument("--themes", default=None)
    ae.add_argument("--people", default=None)
    ae.add_argument("--module", default=None)
    ae.add_argument("--reflection", default=None, help="the companion's logged reflection")
    ae.add_argument("--crisis", action="store_true",
                    help="force crisis_flag=true (model detected a crisis the scanner missed)")
    ae.set_defaults(func=cmd_add_entry)

    ct = sub.add_parser("continuity")
    ct.add_argument("--merge-json", default=None,
                    help="deep-merge a JSON patch (lists append-union); to ACCRETE. Omit both to print.")
    ct.add_argument("--replace-json", default=None,
                    help="overwrite the given top-level keys wholesale; to CORRECT/PRUNE (e.g. edit open_threads)")
    ct.set_defaults(func=cmd_continuity)

    fu = sub.add_parser("followups")
    fu.add_argument("--days", type=int, default=5,
                    help="surface open action-threads not nudged in N days (default 5)")
    fu.set_defaults(func=cmd_followups)

    ca = sub.add_parser("cache")
    ca.add_argument("--module", required=True,
                    help="module name whose state/modules/<module>.yaml to read/merge")
    ca.add_argument("--merge-json", default=None, help="merge a JSON patch; omit to print")
    ca.set_defaults(func=cmd_cache)

    tr = sub.add_parser("trend"); tr.add_argument("--days", type=int, default=30)
    tr.set_defaults(func=cmd_trend)

    jr = sub.add_parser("journal")
    jr.add_argument("--since", default=None, help="YYYY-MM-DD lower bound")
    jr.add_argument("--tag", default=None, help="only entries with this tag")
    jr.add_argument("--limit", type=int, default=20, help="max entries (most recent)")
    jr.set_defaults(func=cmd_journal)

    se = sub.add_parser("search")
    se.add_argument("--tag", default=None); se.add_argument("--text", default=None)
    se.add_argument("--since", default=None); se.add_argument("--until", default=None)
    se.set_defaults(func=cmd_search)

    fg = sub.add_parser("forget")
    fg.add_argument("--birth", action="store_true")
    fg.add_argument("--month", default=None, help="YYYY-MM")
    fg.add_argument("--all", action="store_true")
    fg.add_argument("--yes", action="store_true")
    fg.set_defaults(func=cmd_forget)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
