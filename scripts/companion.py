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
  doctor                    python + dependency status, and what degrades if missing
  brief                     THE every-turn call: status + profile + consent +
                            continuity + due follow-ups + recent entries, in one JSON
  init                      create the private home (idempotent)
  status                    onboarding state, consent, journal counts (slim `brief`)
  read-profile [--json]     dump profile.yaml for the model to load
  set-profile --merge-json  deep-merge a JSON patch into profile.yaml
  consent --set k=yes|no    record consent per category (birth/relationships/mood)
  add-entry --text ...      append a journal entry (prose + atomic index line)
  continuity [--merge-json|--replace-json]   the rolling working memory
  followups [--days N]      open action-threads due for a gentle nudge
  cache --module M          per-module private cache (module writes only its own)
  trend [--days N]          descriptive journal trends (delegates to trends.py)
  journal [--since --tag]   re-read the actual prose entries
  search [--tag --text --since --until]
  forget --birth | --month YYYY-MM | --all --yes
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


from _deps import ensure as _ensure, report as _dep_report  # noqa: E402

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


# Stable identity keys for list-of-dict items. A patched item carrying one of these
# UPDATES the existing item instead of appending a near-duplicate — otherwise editing
# a continuity thread (set last_nudged, close it) silently produces two copies of it,
# and the nudge logic then fires on the stale one forever.
#
# Deliberately only these two. `date` is NOT identity: two relationship incidents can
# share a day, and upserting on it would silently eat one. Anything without a key here
# appends, which is the right default for history.
_LIST_ITEM_KEYS = ("thread", "id")


def _item_key(x):
    if not isinstance(x, dict):
        return None
    for k in _LIST_ITEM_KEYS:
        if k in x and isinstance(x[k], (str, int)):
            return (k, x[k])
    return None


def _merge_list(old, new):
    """Append-UNION with upsert-by-identity-key.

    - identical items are skipped (re-sending the full list is a safe no-op)
    - a dict carrying a stable key (`thread`/`id`) REPLACES the item with the same
      key (so `--merge-json` can edit a thread, not just duplicate it)
    - everything else appends (relationship incidents, moods — history accretes)
    """
    out = list(old)
    index = {}
    for i, x in enumerate(out):
        k = _item_key(x)
        if k is not None and k not in index:
            index[k] = i
    for x in new:
        if x in out:
            continue
        k = _item_key(x)
        if k is not None and k in index:
            out[index[k]] = x
        else:
            if k is not None:
                index[k] = len(out)
            out.append(x)
    return out


def _deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        elif isinstance(v, list) and isinstance(base.get(k), list):
            # Append-UNION + upsert, not replace: preserves accumulated history
            # (relationship incidents, continuity threads) instead of silently
            # dropping it, while letting an edited keyed item update in place.
            base[k] = _merge_list(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Consent enforcement. safety.md §4, SKILL.md and both READMEs all promise that
# birth, relationships and mood are EACH consent-gated and that without consent the
# data "isn't collected, inferred or stored". Only `mood` was ever enforced in code;
# the other two — the most sensitive categories, one of them about a third party who
# never consented to anything — were written on request with no check at all. A
# promise that lives only in prose depends on the model remembering to ask.
CONSENT_GATED = {
    "birth": ("生辰 birth data",
              "profile.birth — 八字/星盘/紫微 all need it, so ask first: "
              "`consent --set birth=yes`"),
    "relationships": ("关系记录 relationship notes",
                      "state/modules/relationships.yaml — this is data about ANOTHER "
                      "person who never consented; ask before storing any of it: "
                      "`consent --set relationships=yes`"),
    "mood": ("情绪 mood", "journal mood values: `consent --set mood=yes`"),
}


def _granted(home, category):
    return _load_yaml(_paths(home)["consent"]).get(category, {}).get("granted") is True


def _refuse_ungated(home, category):
    """Return a refusal payload when `category` is not consented, else None.

    Fails CLOSED: at init `granted` is None (never asked), which is NOT consent.
    """
    if _granted(home, category):
        return None
    label, where = CONSENT_GATED[category]
    return {
        "ok": False,
        "error": f"consent.{category} not granted — refusing to store {label}",
        "refused_because": ("safety.md §4: no consent → don't collect, infer, or store "
                            "that category. This is enforced here, not just asked of you."),
        "where": where,
        "_next": (f"Ask the person plainly first, then `consent --set {category}=yes`. "
                  f"If they decline, skip the module gracefully — don't work around this."),
    }


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


# ---------------------------------------------------------------------------
# City -> IANA timezone, offline.
#
# `identity.timezone` drives daily timing AND which crisis helpline the person is
# offered, and onboarding asks them for it in plain words ("柏林", "New York"). The
# onboarding form used to map only two hard-coded countries, so everyone else had the
# city they typed silently thrown away. zoneinfo ships the full IANA list, and most
# zones are named after a city — that makes a real, general, offline resolver.
_TZ_ALIASES = {
    "北京": "Asia/Shanghai", "上海": "Asia/Shanghai", "广州": "Asia/Shanghai",
    "深圳": "Asia/Shanghai", "杭州": "Asia/Shanghai", "成都": "Asia/Shanghai",
    "中国": "Asia/Shanghai", "香港": "Asia/Hong_Kong", "澳门": "Asia/Macau",
    "台北": "Asia/Taipei", "台湾": "Asia/Taipei",
    "东京": "Asia/Tokyo", "日本": "Asia/Tokyo", "首尔": "Asia/Seoul", "韩国": "Asia/Seoul",
    "新加坡": "Asia/Singapore", "曼谷": "Asia/Bangkok", "迪拜": "Asia/Dubai",
    "阿姆斯特丹": "Europe/Amsterdam", "荷兰": "Europe/Amsterdam",
    "莱顿": "Europe/Amsterdam", "鹿特丹": "Europe/Amsterdam", "海牙": "Europe/Amsterdam",
    "柏林": "Europe/Berlin", "德国": "Europe/Berlin", "慕尼黑": "Europe/Berlin",
    "巴黎": "Europe/Paris", "法国": "Europe/Paris",
    "伦敦": "Europe/London", "英国": "Europe/London",
    "马德里": "Europe/Madrid", "西班牙": "Europe/Madrid",
    "罗马": "Europe/Rome", "意大利": "Europe/Rome",
    "苏黎世": "Europe/Zurich", "瑞士": "Europe/Zurich",
    "斯德哥尔摩": "Europe/Stockholm", "哥本哈根": "Europe/Copenhagen",
    "布鲁塞尔": "Europe/Brussels", "维也纳": "Europe/Vienna", "莫斯科": "Europe/Moscow",
    "纽约": "America/New_York", "波士顿": "America/New_York", "华盛顿": "America/New_York",
    "芝加哥": "America/Chicago", "洛杉矶": "America/Los_Angeles",
    "旧金山": "America/Los_Angeles", "西雅图": "America/Los_Angeles",
    "温哥华": "America/Vancouver", "多伦多": "America/Toronto", "加拿大": "America/Toronto",
    "悉尼": "Australia/Sydney", "墨尔本": "Australia/Melbourne", "澳大利亚": "Australia/Sydney",
    "奥克兰": "Pacific/Auckland", "新西兰": "Pacific/Auckland",
    # English country/region words that aren't zone names
    "usa": "America/New_York", "united states": "America/New_York", "uk": "Europe/London",
    "england": "Europe/London", "britain": "Europe/London", "ireland": "Europe/Dublin",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "spain": "Europe/Madrid",
    "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam", "holland": "Europe/Amsterdam",
    "belgium": "Europe/Brussels", "switzerland": "Europe/Zurich", "sweden": "Europe/Stockholm",
    "japan": "Asia/Tokyo", "korea": "Asia/Seoul", "china": "Asia/Shanghai",
    "india": "Asia/Kolkata", "australia": "Australia/Sydney", "canada": "America/Toronto",
    "brazil": "America/Sao_Paulo", "mexico": "America/Mexico_City",
}


def _fold(s):
    """lowercase + strip diacritics, so "São Paulo" reaches America/Sao_Paulo and
    "Zurich" reaches Europe/Zurich. A companion for one person anywhere has to match
    the way that person actually spells their own city."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))


def resolve_timezone(text, limit=5):
    """Best-effort city/country -> IANA zone, offline. Returns ranked candidates.

    Empty list when nothing matches — that is the honest answer. Never fall back to a
    default zone: a wrong timezone means a wrong daily chart and, worse, the wrong
    country's crisis helpline.
    """
    if not text or not text.strip():
        return []
    raw = text.strip()
    low = _fold(raw)
    hits, seen = [], set()

    def add(zone, score, why):
        if zone and zone not in seen:
            seen.add(zone)
            hits.append({"timezone": zone, "score": score, "matched_on": why})

    # 1. explicit alias (Chinese city names, country words)
    for k, v in _TZ_ALIASES.items():
        if k in low or k in raw:
            add(v, 1.0, f"alias:{k}")

    # 2. the text already IS a zone name
    try:
        from zoneinfo import available_timezones, ZoneInfo
        zones = available_timezones()
    except Exception:  # pragma: no cover - zoneinfo missing/no tzdata
        return hits[:limit]
    for z in zones:
        if z.lower() == low:
            add(z, 1.0, "exact zone name")

    # 3. the last path segment is a city: America/New_York -> "new york"/"new_york"
    token = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", low).strip()
    for z in sorted(zones):
        city = z.rsplit("/", 1)[-1]
        city_words = re.sub(r"[^a-z0-9]+", " ", _fold(city)).strip()
        if not city_words:
            continue
        if city_words == token:
            add(z, 0.95, f"city:{city}")
        elif token and (token.startswith(city_words + " ") or f" {city_words} " in f" {token} "):
            add(z, 0.7, f"city:{city}")
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def cmd_resolve_tz(args):
    """Map what a person calls their location to an IANA timezone, offline.

    `identity.timezone` decides daily timing and which crisis line they're offered, so
    a wrong guess is worse than no answer: with no match this returns an empty list and
    tells you to ask, rather than defaulting to anything."""
    hits = resolve_timezone(args.place)
    print(json.dumps({
        "query": args.place, "candidates": hits,
        "_note": ("Confirm with the person if more than one is plausible, then store it: "
                  "`set-profile --merge-json '{\"identity\":{\"timezone\":\"<zone>\"}}'`."
                  if hits else
                  "No match — ASK for a nearby major city or the country. Do NOT guess a "
                  "timezone: it drives the daily chart and which crisis helpline they get."),
    }, ensure_ascii=False, indent=2))


def cmd_doctor(args):
    """Is this machine able to run the skill? Reports Python + every dependency with
    the exact install command and what degrades without it. Installs nothing.
    Run this first on an unfamiliar machine or in a sandbox."""
    rep = _dep_report()
    home = home_dir(args.home)
    rep["home"] = home
    rep["home_exists"] = os.path.exists(home)
    if os.name == "nt":
        rep["platform_note"] = ("Windows: use `python` (or `py -3`) instead of `python3`, "
                                "and note COMPANION_HOME cannot be chmod 700 here — the "
                                "'only you can read it' guarantee is POSIX-only. Say so "
                                "rather than repeating the stronger claim.")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


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
    if isinstance(patch.get("birth"), dict) and any(
            v is not None for v in patch["birth"].values()):
        refusal = _refuse_ungated(home, "birth")
        if refusal:
            print(json.dumps(refusal, ensure_ascii=False, indent=2))
            raise SystemExit(3)
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

    --merge-json  deep-merges. Lists append, EXCEPT that an item carrying a stable
                  key (`thread`, `id`) updates the matching item in place — so this
                  both accretes (a new thread, a mood) and edits (re-send a thread
                  with last_nudged set, or status: done). It cannot REMOVE an item.
    --replace-json OVERWRITES each given top-level key outright — use to PRUNE: drop a
                  resolved thread, rewrite the rolling_summary. Send the full intended
                  value of each key you touch.
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


_FOLLOWUP_NOTE = ("Gently follow up on at most ONE of these (nudge, don't nag; never in a "
                  "crisis or a purely light moment). After following up, record it: "
                  "`continuity --merge-json` with just that thread, its `thread` key "
                  "unchanged and `last_nudged` set to today (or `status: done`) — the "
                  "matching thread is updated in place, not duplicated.")


def _due_followups(home, days=5):
    """Open action-threads DUE for a gentle nudge — turns continuity from passive
    memory into follow-through. A thread is due if it isn't closed and hasn't been
    nudged in `days`. Thread shape:
    {thread, action, opened, last_nudged, status: open|in_progress|done}."""
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
        if since is None or since >= days:
            due.append({"thread": t.get("thread"), "action": t.get("action"),
                        "status": t.get("status") or "open", "opened": t.get("opened"),
                        "days_open": _age(t.get("opened")), "days_since_nudge": since})
    return due


def cmd_followups(args):
    """Read-only; the model does the warm follow-up, then records it via `continuity`."""
    due = _due_followups(home_dir(args.home), args.days)
    print(json.dumps({"due": due, "count": len(due), "_note": _FOLLOWUP_NOTE},
                     ensure_ascii=False, indent=2))


def cmd_brief(args):
    """ONE call for the every-turn protocol: state + who they are + working memory +
    what's due. Replaces status + read-profile + reading continuity.yaml + followups,
    so the protocol is one command that is hard to half-run.

    Everything here is read-only. `--full-profile` includes the whole profile (the
    default trims the free-form `context` block, which can be long)."""
    home = home_dir(args.home)
    p = _paths(home)
    if not os.path.exists(p["profile"]):
        print(json.dumps({
            "initialized": False, "home": home,
            "_next": "Not set up yet. Run `init`, then onboard (references/onboarding.md; "
                     "prefer the HTML form). Never force onboarding during a crisis.",
        }, ensure_ascii=False, indent=2))
        return

    prof = _load_yaml(p["profile"], {})
    consent = _load_yaml(p["consent"], {})
    cont = _load_yaml(p["continuity"], {})
    rows = trends_mod._load(p["index"])
    if not args.full_profile:
        prof = {k: v for k, v in prof.items() if k != "context"}

    recent = []
    for r in rows[-args.recent:] if args.recent else []:
        recent.append({k: r.get(k) for k in ("date", "mood", "tags", "themes", "crisis_flag")})

    due = _due_followups(home, args.days)
    out = {
        "initialized": True,
        "home": home,
        "onboarding_complete": prof.get("onboarding_complete"),
        "consent": {k: v.get("granted") for k, v in consent.items()},
        "profile": prof,
        "continuity": {
            "rolling_summary": cont.get("rolling_summary"),
            "open_threads": cont.get("open_threads") or [],
            "recent_moods": cont.get("recent_moods") or [],
            "updated": cont.get("updated"),
        },
        "followups_due": due,
        "journal": {
            "entries": len(rows),
            "last_entry": rows[-1]["date"] if rows and rows[-1].get("date") else None,
            "recent": recent,
        },
        "_note": _FOLLOWUP_NOTE if due else
                 "Nothing due for follow-up. Don't manufacture a callback.",
    }
    if any(r.get("crisis_flag") for r in rows[-args.recent:]) if args.recent else False:
        out["_crisis_recent"] = ("A recent entry carries crisis_flag. Read "
                                 "references/safety.md §2 before replying; never reopen "
                                 "it as casual small talk or with a fortune framing.")
    print(json.dumps(out, ensure_ascii=False, indent=2))


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
        if args.module in CONSENT_GATED:
            refusal = _refuse_ungated(home, args.module)
            if refusal:
                print(json.dumps(refusal, ensure_ascii=False, indent=2))
                raise SystemExit(3)
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

    # mood is a 0–10 scale (profile-schema.md). Reject out-of-range LOUDLY rather than
    # storing it: a stray value silently poisons every mood_avg / direction that
    # `trend` reports, and those are presented to the person as computed FACTS.
    mood = args.mood
    if mood is not None and not (0 <= mood <= 10):
        print(json.dumps({"ok": False, "error": f"--mood must be 0..10 (got {mood})"},
                         ensure_ascii=False))
        return

    # mood is gated by consent.mood — fail CLOSED: store only when explicitly granted
    # (at init `granted` is None = never asked, so mood must NOT be stored yet).
    # Report the drop; a silent one makes the model tell the person it logged a mood
    # that is not in the file.
    dropped = []
    if mood is not None and consent.get("mood", {}).get("granted") is not True:
        mood = None
        dropped.append("mood — consent.mood not granted (ask, then "
                       "`consent --set mood=yes`); the entry text was still saved")

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

    out = {"ok": True, "date": date, "file": row["file"], "mood": mood,
           "crisis_flag": crisis_flag, "crisis_forced": bool(args.crisis),
           "safety_scan": scan}
    if dropped:
        out["dropped"] = dropped
        out["_note"] = ("Something you passed was NOT stored (see `dropped`). Don't tell "
                        "the person it was logged.")
    print(json.dumps(out, ensure_ascii=False, indent=2))


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
        # `--yes` alone was the ONLY guard, so this would rmtree whatever COMPANION_HOME
        # (or --home) happened to point at — a typo, a stale export, or a shell variable
        # meant for something else took an unrelated directory with it. Refuse anything
        # that is not recognisably a companion home: init writes README.txt and
        # consent.yaml, so requiring them makes the target prove what it is.
        markers = [p["readme"], p["consent"], p["profile"]]
        present = [m for m in markers if os.path.exists(m)]
        if len(present) < 2:
            print(json.dumps({
                "ok": False,
                "error": f"refusing to wipe {home}: it does not look like a companion home",
                "why": ("--all deletes the directory RECURSIVELY. It must contain at least "
                        "two of README.txt / consent.yaml / profile.yaml, which `init` "
                        "writes. Found: " + (", ".join(os.path.basename(m) for m in present)
                                             or "none")),
                "_next": ("Check COMPANION_HOME / --home. If you really meant this "
                          "directory, run `init` in it first, or delete it yourself — "
                          "this tool will not remove a directory it did not create."),
            }, ensure_ascii=False, indent=2))
            raise SystemExit(3)
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
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    rt = sub.add_parser("resolve-tz", help="city/country -> IANA timezone (offline)")
    rt.add_argument("place", help="what the person called their location, e.g. 柏林 / New York")
    rt.set_defaults(func=cmd_resolve_tz)

    br = sub.add_parser("brief", help="every-turn snapshot in ONE call "
                                      "(status + profile + continuity + due follow-ups)")
    br.add_argument("--days", type=int, default=5, help="follow-up nudge threshold")
    br.add_argument("--recent", type=int, default=5, help="how many recent journal rows")
    br.add_argument("--full-profile", action="store_true",
                    help="include the free-form `context` block too")
    br.set_defaults(func=cmd_brief)

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
