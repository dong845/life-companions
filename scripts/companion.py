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
  lunar-to-solar Y M D [--leap]  a lunar-calendar birthday -> the solar date the engines take
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
  forget --birth | --month YYYY-MM | --entry DATE [--nth N] | --person NAME
         [--with-entries] | --relationships | --mood | --all --yes
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
# Module caches that hold a consent-gated category. The destiny cache stores the pillars,
# which are birth data in another form, so it is gated by birth consent like profile.birth.
_MODULE_CONSENT = {"relationships": "relationships", "destiny": "birth"}


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


# How each category is deleted, for every message that has to say so.
_FORGET_CMD = {
    "birth": "`forget --birth`",
    "relationships": "`forget --relationships`, or `forget --person NAME` for one person",
    "mood": "`forget --mood`",
}


def _relationships_path(home):
    return os.path.join(_paths(home)["modules"], "relationships.yaml")


def _retained(home):
    """What is still stored in each consent category, in words.

    Revoking consent stops use; it does not delete. Whoever revokes has to be told what is
    still on disk and how to remove it, or "I withdrew consent" quietly means "it is all
    still there"."""
    p = _paths(home)
    out = {}
    birth = _load_yaml(p["profile"]).get("birth") or {}
    n_birth = sum(1 for k, v in birth.items() if k != "conventions" and v is not None)
    cached_chart = os.path.exists(os.path.join(p["modules"], "destiny.yaml"))
    if n_birth or cached_chart:
        out["birth"] = (f"{n_birth} birth field(s) in profile.yaml"
                        + ("; a cached chart in state/modules/destiny.yaml" if cached_chart else ""))
    rows = trends_mod._load(p["index"])
    rel = _relationships_path(home)
    people = (_load_yaml(rel).get("people") or {}) if os.path.exists(rel) else {}
    naming = sum(1 for r in rows if r.get("people"))
    if people or naming:
        out["relationships"] = (f"{len(people)} tracked person/people in "
                                f"state/modules/relationships.yaml; {naming} journal row(s) "
                                "naming someone")
    moods = sum(1 for r in rows if r.get("mood") is not None)
    if moods:
        out["mood"] = f"{moods} mood value(s) in the journal"
    return out


def _refuse_ungranted_read(home, category):
    """Reads are gated as well as writes. A revoked category that is still read keeps
    reaching every reply, and that is not what revoking means."""
    if _granted(home, category):
        return None
    label, _ = CONSENT_GATED[category]
    return {
        "ok": False,
        "error": f"consent.{category} is not granted — {label} is withheld",
        "retained": _retained(home).get(category),
        "_next": (f"Don't use it. Ask first: `consent --set {category}=yes` restores access "
                  f"to what is stored. To delete it instead: {_FORGET_CMD[category]}."),
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


def lunar_to_solar(year, month, day, leap=False):
    """A lunar-calendar date -> (solar ISO date, None), or (None, reason) when that lunar
    date does not exist. Every engine takes a solar date, and converting by hand is what
    this skill forbids: leap months and 29-day months are exactly where it goes wrong."""
    if not 1 <= month <= 12:
        return None, f"农历月份只有 1–12（收到 {month}）"
    if not 1 <= day <= 30:
        return None, f"农历日期只有 1–30（收到 {day}）"
    lp = _ensure("lunar-python", "lunar_python")
    try:
        leap_month = lp.LunarYear.fromYear(year).getLeapMonth()
        if leap and leap_month != month:
            return None, (f"{year} 年没有闰月" if not leap_month else
                          f"{year} 年没有闰{month}月（这一年闰的是 {leap_month} 月）")
        days = lp.LunarMonth.fromYm(year, -month if leap else month).getDayCount()
        if day > days:
            return None, f"{year} 年{'闰' if leap else ''}{month}月只有 {days} 天（收到 {day}）"
        solar = lp.Lunar.fromYmd(year, -month if leap else month, day).getSolar()
    except Exception as e:      # lunar-python raises a bare Exception for dates it can't place
        return None, f"无法换算农历 {year} 年 {month} 月 {day} 日：{e}"
    return solar.toYmd(), None


def cmd_lunar_to_solar(args):
    solar, why = lunar_to_solar(args.year, args.month, args.day, args.leap)
    if why:
        print(json.dumps({
            "ok": False, "error": why,
            "_next": ("Check the date with the person. A lunar month has 29 or 30 days, and only "
                      "some years have a leap month — one at most."),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps({
        "ok": True, "solar": solar,
        "lunar": {"year": args.year, "month": args.month, "day": args.day,
                  "leap": bool(args.leap)},
        "_note": ("Converted on the Chinese calendar's own day boundary. For a birth abroad "
                  "close to midnight the solar date can be a day off, so confirm it with the "
                  "person. Store the solar date as birth.date and this input as "
                  "birth.date_input."),
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
    withdrawn = []
    for pair in args.set:
        k, _, v = pair.partition("=")
        k = k.strip()
        granted = v.strip().lower() in ("yes", "true", "1", "y")
        if not granted:
            withdrawn.append(k)
        consent[k] = {"granted": granted, "date": _today()}
    _save_yaml(p["consent"], consent)
    out = {"ok": True, "consent": {k: val.get("granted") for k, val in consent.items()}}
    # Saying no stops every script from reading or using the category (see
    # _refuse_ungranted_read). It deletes nothing, so a mistaken revoke costs nothing, and
    # the reply has to say what is still stored and how to remove it.
    kept = {k: v for k, v in _retained(home).items() if k in withdrawn}
    if kept:
        out["retained"] = kept
        out["_next"] = ("Nothing was deleted. The scripts stop using these now; tell the "
                        "person that plainly, and how to delete them if they want: "
                        + "; ".join(f"{k} → {_FORGET_CMD[k]}" for k in kept))
    print(json.dumps(out, ensure_ascii=False))


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

    # A category without consent is withheld here, not deleted. Every reply is built from
    # brief, so this is where "revoked" has to take effect.
    ok = {c: _granted(home, c) for c in CONSENT_GATED}
    consent_notes = []
    birth = prof.get("birth") or {}
    if not ok["birth"] and any(v is not None for k, v in birth.items() if k != "conventions"):
        prof["birth"] = {k: (v if k == "conventions" else None) for k, v in birth.items()}
        prof["birth"]["_withheld"] = True
        consent_notes.append("consent.birth is not granted: the stored birth fields are withheld, "
                             "not deleted. Don't chart without asking; `consent --set birth=yes` "
                             "restores them and `forget --birth` deletes them.")
    if not ok["mood"] and (cont.get("recent_moods")
                           or any(r.get("mood") is not None for r in rows)):
        consent_notes.append("consent.mood is not granted: mood values are withheld, not "
                             "deleted; `forget --mood` deletes them.")
    if not ok["relationships"] and _retained(home).get("relationships"):
        consent_notes.append("consent.relationships is not granted: relationship records are "
                             "withheld, and the rolling summary or threads may still mention "
                             "people, so don't draw on those. `forget --relationships` or "
                             "`forget --person NAME` deletes them.")

    recent = []
    for r in rows[-args.recent:] if args.recent else []:
        row = {k: r.get(k) for k in ("date", "mood", "tags", "themes", "crisis_flag")}
        if not ok["mood"]:
            row["mood"] = None
        recent.append(row)

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
            "recent_moods": (cont.get("recent_moods") or []) if ok["mood"] else [],
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
    if consent_notes:
        out["_consent_notes"] = consent_notes
    crisis_recent = any(r.get("crisis_flag") for r in rows[-args.recent:]) if args.recent else False
    # Two weeks of mostly low moods is not a crisis, and it deserves more than a fortune card
    # every morning (safety.md §2b). Only with mood consent, never over a crisis, and at
    # most once a week.
    if ok["mood"] and not crisis_recent:
        low = trends_mod._sustained_low(rows)
        try:
            checked = datetime.date.fromisoformat(str(cont.get("wellbeing_checked") or "")[:10])
            asked_recently = (datetime.date.today() - checked).days < 7
        except ValueError:
            asked_recently = False
        if low["triggered"] and not asked_recently:
            out["_wellbeing_check"] = {
                "why": (f"{low['entries']} moods logged in the last {low['window_days']} days, "
                        f"more than half of them {low['low_max']}/10 or lower"),
                "_next": ("Care before any reading. Set the fortune voice aside unless they ask "
                          "for it, ask plainly how they have been, and where it fits suggest "
                          "talking it through with someone they trust or a professional. This "
                          "is not a crisis script: crisis lines only with crisis signals "
                          "(safety.md §2). Don't read the numbers back as a verdict. "
                          "Afterwards: continuity --merge-json "
                          "'{\"wellbeing_checked\": \"YYYY-MM-DD\"}'."),
            }
    if crisis_recent:
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
    category = _MODULE_CONSENT.get(args.module)
    if not args.merge_json and category:
        refusal = _refuse_ungranted_read(home, category)
        if refusal:
            print(json.dumps(refusal, ensure_ascii=False, indent=2))
            raise SystemExit(3)
    data = _load_yaml(path, {})
    if args.merge_json:
        if category:
            refusal = _refuse_ungated(home, category)
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

    # Another person's name is relationship data about someone who never consented, and
    # `people` is exactly what pattern-tracking counts, so it is gated like that cache.
    if people and consent.get("relationships", {}).get("granted") is not True:
        people = []
        dropped.append("people — consent.relationships not granted (other people's names "
                       "belong to that category; ask, then `consent --set relationships=yes`); "
                       "the entry text was still saved")

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


_MOOD_IN_HEADER = re.compile(r"^(## [^\n]*?) · mood \d+/10", re.M)


def _write_index(home, rows):
    _atomic_write(_paths(home)["index"],
                  "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _entry_text(home, row):
    """The prose of one journal entry, found through its index row."""
    if not row.get("file"):
        return ""
    fp = os.path.join(home, row["file"])
    if not os.path.exists(fp):
        return ""
    with open(fp, encoding="utf-8") as f:
        block = f.read()[row.get("offset") or 0:]
    nxt = block.find("\n## ", 1)
    return block[:nxt] if nxt != -1 else block


def _nth_of_day(rows, row):
    same = [r for r in rows if r.get("date") == row.get("date")]
    return next((i for i, r in enumerate(same, 1) if r is row), None)


def _preview(home, row, width=30):
    lines = _entry_text(home, row).splitlines()[1:]
    body = " ".join(l.strip() for l in lines
                    if l.strip() and not l.startswith(("tags:", "> companion:")))
    return body[:width]


def _rewrite_journal(home, transform, files=None):
    """Rewrite journal files entry by entry and keep every index offset true.

    `transform(row, block)` returns the entry's new text, or None to delete it. Every row
    in a touched file is checked against that file BEFORE anything is written, and one
    mismatch aborts the lot: a hand-edited file refuses loudly instead of deleting the
    wrong words. Returns (rows_after, None) or (None, error)."""
    p = _paths(home)
    rows = trends_mod._load(p["index"])
    by_file = {}
    for r in rows:
        by_file.setdefault(r.get("file"), []).append(r)
    after, plans = {}, {}
    for f, frs in by_file.items():
        if files is not None and f not in files:
            continue
        path = os.path.join(home, f or "")
        if not f or not os.path.exists(path):
            return None, f"the index points at {f}, which is missing. Nothing was changed."
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        for r in frs:
            off = r.get("offset")
            if (not isinstance(off, int)
                    or not content[off:].lstrip("\n").startswith(f"## {r.get('date')}")):
                return None, (f"the index no longer matches {f}: the entry for "
                              f"{r.get('date')} is not at offset {off}. Nothing was changed. "
                              "The file looks hand-edited; `forget --month` still removes the "
                              "whole month, or fix the file by hand.")
        frs = sorted(frs, key=lambda r: r["offset"])
        pieces = [content[:frs[0]["offset"]]]
        pos = len(pieces[0])
        for i, r in enumerate(frs):
            end = frs[i + 1]["offset"] if i + 1 < len(frs) else len(content)
            new = transform(r, content[r["offset"]:end])
            if new is None:
                after[id(r)] = None
                continue
            after[id(r)] = dict(r, offset=pos)
            pieces.append(new)
            pos += len(new)
        plans[path] = "".join(pieces)
    for path, text in plans.items():
        if text.strip():
            _atomic_write(path, text)
        elif os.path.exists(path):
            os.remove(path)
    out = [after[id(r)] if id(r) in after else r for r in rows]
    out = [r for r in out if r is not None]
    _write_index(home, out)
    return out, None


def _residue(home, needle):
    """Files under home that still contain `needle`, so a delete can check its own work."""
    hits = []
    for dp, _dn, fn in os.walk(home):
        for name in fn:
            fp = os.path.join(dp, name)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    if needle in fh.read():
                        hits.append(os.path.relpath(fp, home))
            except OSError:
                continue
    return sorted(hits)


def _drop_form_marker(home, predicate):
    """Delete .form_result.json when predicate(its JSON) is true."""
    marker = os.path.join(home, ".form_result.json")
    if not os.path.exists(marker):
        return False
    try:
        with open(marker, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not predicate(data if isinstance(data, dict) else {}):
        return False
    os.remove(marker)
    return True


def _set_consent(home, category, granted):
    p = _paths(home)
    consent = _load_yaml(p["consent"])
    consent[category] = {"granted": granted, "date": _today()}
    _save_yaml(p["consent"], consent)


def _forget_birth(home):
    p = _paths(home)
    prof = _load_yaml(p["profile"])
    prof["birth"] = {"date": None, "time": None, "time_known": None, "place": None,
                     "lat": None, "lon": None, "tz_at_birth": None,
                     "conventions": (prof.get("birth") or {}).get("conventions", {})}
    prof["updated"] = _today()
    _save_yaml(p["profile"], prof)
    _set_consent(home, "birth", False)
    destiny = os.path.join(p["modules"], "destiny.yaml")        # the cached natal chart
    if os.path.exists(destiny):
        os.remove(destiny)
    done = ["birth data + cached charts removed"]
    # The onboarding form's result marker carried the birth date, and nothing deleted it.
    if _drop_form_marker(home, lambda m: m.get("form") == "onboarding" or m.get("birth_date")):
        done.append("onboarding form result marker removed")
    return done


def _forget_month(home, month):
    p = _paths(home)
    done = []
    month_path = os.path.join(p["journal"], f"{month}.md")
    if os.path.exists(month_path):
        os.remove(month_path)
    rows = trends_mod._load(p["index"])
    kept = [r for r in rows if not str(r.get("date", "")).startswith(month)]
    _write_index(home, kept)
    done.append(f"journal {month} removed ({len(rows) - len(kept)} entries)")

    # What the companion learned that month also sits in the relationship log, the working
    # memory and the module caches. Deleting only the journal left it all in place.
    rel = _relationships_path(home)
    if os.path.exists(rel):
        data = _load_yaml(rel)
        people = data.get("people") or {}
        gone, trimmed, n = [], [], 0
        for name in list(people):
            rec = people[name]
            incs = rec.get("incidents") if isinstance(rec, dict) else None
            if not isinstance(incs, list):
                continue
            keep = [i for i in incs
                    if not (isinstance(i, dict) and str(i.get("date", "")).startswith(month))]
            if len(keep) == len(incs):
                continue
            n += len(incs) - len(keep)
            if keep:
                rec["incidents"] = keep
                trimmed.append(name)
            else:
                # known only from that month: their tendencies and patterns can only have
                # come from it, so nothing about them survives
                del people[name]
                gone.append(name)
        if n:
            _save_yaml(rel, data)
            done.append(f"{n} relationship incident(s) from {month} removed"
                        + (f"; {len(gone)} person/people known only from {month} removed"
                           if gone else ""))
        if trimmed:
            done.append("kept the rest of " + "、".join(trimmed) + "'s record: tendencies and "
                        f"patterns may partly come from {month}; `forget --person NAME` "
                        "removes a whole record")

    cont = _load_yaml(p["continuity"], {})
    if cont:
        threads = cont.get("open_threads") or []
        keep = [t for t in threads
                if not (isinstance(t, dict) and str(t.get("opened", "")).startswith(month))]
        changed = len(keep) != len(threads)
        if changed:
            cont["open_threads"] = keep
            done.append(f"{len(threads) - len(keep)} follow-up thread(s) opened in {month} removed")
        # The rolling summary and recent moods carry no dates. Written during or after that
        # month, they may quote it and there is no way to tell, so they go; they rebuild.
        updated = str(cont.get("updated") or "")
        if not updated or updated >= f"{month}-01":
            for key, empty in (("rolling_summary", ""), ("recent_moods", [])):
                if cont.get(key):
                    cont[key] = empty
                    changed = True
                    done.append(f"continuity {key} cleared (it may have drawn on {month})")
        if changed:
            cont["updated"] = _today()
            _save_yaml(p["continuity"], cont)

    intake = os.path.join(p["modules"], "career_intake.yaml")
    if os.path.exists(intake):
        data = _load_yaml(intake)
        if str((data.get("latest") or {}).get("ts", "")).startswith(month):
            data.pop("latest", None)
            _save_yaml(intake, data)
            done.append(f"career check submitted in {month} removed")
    if _drop_form_marker(home, lambda m: str(m.get("ts", "")).startswith(month)):
        done.append(f"form result marker from {month} removed")
    return done


def _forget_entry(home, date, nth):
    rows = trends_mod._load(_paths(home)["index"])
    day = [r for r in rows if str(r.get("date")) == date]
    if not day:
        return None, {"ok": False, "error": f"no journal entry on {date}"}
    if len(day) > 1 and nth is None:
        return None, {"ok": False,
                      "error": f"{len(day)} entries on {date}; say which one with --nth",
                      "candidates": [{"nth": i, "preview": _preview(home, r)}
                                     for i, r in enumerate(day, 1)]}
    n = nth or 1
    if not 1 <= n <= len(day):
        return None, {"ok": False,
                      "error": f"--nth {n} is out of range: {date} has {len(day)} entr"
                               f"{'y' if len(day) == 1 else 'ies'}"}
    target = day[n - 1]
    key = (target.get("file"), target.get("offset"))
    _, err = _rewrite_journal(
        home, lambda r, b: None if (r.get("file"), r.get("offset")) == key else b,
        files={target.get("file")})
    if err:
        return None, {"ok": False, "error": err}
    return [f"journal entry {date} #{n} deleted"
            + (" (it carried a crisis flag)" if target.get("crisis_flag") else "")], None


def _forget_person(home, name, with_entries):
    p = _paths(home)
    done, report = [], {}
    rel = _relationships_path(home)
    if os.path.exists(rel):
        data = _load_yaml(rel)
        people = data.get("people") or {}
        if name in people:
            del people[name]
            _save_yaml(rel, data)
            done.append(f"relationship record for {name} removed")

    # Prose, threads and the summary are matched by substring, which is only safe for a
    # name of two characters or more; a one-letter name would match half the journal.
    searchable = len(name) >= 2
    if not searchable:
        report["_note"] = (f"「{name}」 is a single character, so journal prose, threads and "
                           "the summary were not searched; check those by hand.")
    rows = trends_mod._load(p["index"])
    mentioning = [r for r in rows if name in (r.get("people") or [])
                  or (searchable and name in _entry_text(home, r))]
    if with_entries and mentioning:
        keys = {(r.get("file"), r.get("offset")) for r in mentioning}
        _, err = _rewrite_journal(
            home, lambda r, b: None if (r.get("file"), r.get("offset")) in keys else b,
            files={r.get("file") for r in mentioning})
        if err:
            return None, {"ok": False, "error": err}
        done.append(f"{len(mentioning)} journal entr{'y' if len(mentioning) == 1 else 'ies'} "
                    f"mentioning {name} deleted")
    elif mentioning:
        report["entries_still_mentioning"] = [
            {"date": r.get("date"), "nth": _nth_of_day(rows, r)} for r in mentioning]
        report["_next"] = (f"Their prose was kept. `forget --person {name} --with-entries` "
                           "deletes those entries too, or `forget --entry DATE --nth N` one "
                           "at a time.")

    rows = trends_mod._load(p["index"])
    if any(name in (r.get("people") or []) for r in rows):
        for r in rows:
            if r.get("people"):
                r["people"] = [x for x in r["people"] if x != name]
        _write_index(home, rows)
        done.append(f"{name} removed from the journal index")

    cont = _load_yaml(p["continuity"], {})
    if cont and searchable:
        threads = cont.get("open_threads") or []
        keep = [t for t in threads if name not in str(t)]
        cleared = name in str(cont.get("rolling_summary") or "")
        if cleared:
            cont["rolling_summary"] = ""
            done.append("continuity rolling_summary cleared (it mentioned them; it rebuilds)")
        if len(keep) != len(threads):
            cont["open_threads"] = keep
            done.append(f"{len(threads) - len(keep)} follow-up thread(s) about {name} removed")
        if cleared or len(keep) != len(threads):
            cont["updated"] = _today()
            _save_yaml(p["continuity"], cont)

    if searchable:
        left = [f for f in _residue(home, name)
                if with_entries or not f.startswith("journal" + os.sep)]
        if left:
            report["still_mentioned_in"] = left
    report["done"] = done
    return report, None


def _forget_relationships(home):
    p = _paths(home)
    done = []
    rel = _relationships_path(home)
    if os.path.exists(rel):
        os.remove(rel)
        done.append("state/modules/relationships.yaml deleted")
    rows = trends_mod._load(p["index"])
    named = sum(1 for r in rows if r.get("people"))
    if named:
        for r in rows:
            r["people"] = []
        _write_index(home, rows)
        done.append(f"names removed from {named} journal row(s)")
    _set_consent(home, "relationships", False)
    done.append("consent.relationships revoked")
    note = ("The prose of those journal entries was kept, and the rolling summary or threads "
            "may still mention people. `forget --person NAME --with-entries` removes one "
            "person completely." if named else None)
    return done, note


def _forget_mood(home):
    p = _paths(home)
    rows, err = _rewrite_journal(home, lambda r, b: _MOOD_IN_HEADER.sub(r"\1", b, count=1))
    if err:
        return None, {"ok": False, "error": err}
    n = sum(1 for r in rows if r.get("mood") is not None)
    for r in rows:
        r["mood"] = None
    _write_index(home, rows)
    done = [f"{n} mood value(s) removed from the journal"]
    cont = _load_yaml(p["continuity"], {})
    if cont.get("recent_moods"):
        cont["recent_moods"] = []
        cont["updated"] = _today()
        _save_yaml(p["continuity"], cont)
        done.append("continuity recent_moods cleared")
    _set_consent(home, "mood", False)
    done.append("consent.mood revoked")
    return done, None


def cmd_forget(args):
    home = home_dir(args.home)
    p = _paths(home)
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

    def usage(msg):
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        raise SystemExit(2)

    def refuse(payload):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(3)

    if args.month and not re.fullmatch(r"\d{4}-\d{2}", args.month):
        usage(f"--month must be YYYY-MM (got {args.month!r})")
    if args.entry:
        try:
            datetime.date.fromisoformat(args.entry)
        except ValueError:
            usage(f"--entry must be YYYY-MM-DD (got {args.entry!r})")
    if args.nth is not None and not args.entry:
        usage("--nth only makes sense with --entry")
    if args.with_entries and not args.person:
        usage("--with-entries only makes sense with --person NAME")
    if args.person is not None and not args.person.strip():
        usage("--person needs a name")

    done, extra = [], {}
    if args.birth:
        done += _forget_birth(home)
    if args.month:
        done += _forget_month(home, args.month)
    if args.entry:
        d, err = _forget_entry(home, args.entry, args.nth)
        if err:
            refuse(err)
        done += d
    if args.person:
        rep, err = _forget_person(home, args.person.strip(), args.with_entries)
        if err:
            refuse(err)
        done += rep.pop("done")
        extra.update(rep)
    if args.relationships:
        d, note = _forget_relationships(home)
        done += d
        if note:
            extra["_note"] = note
    if args.mood:
        d, err = _forget_mood(home)
        if err:
            refuse(err)
        done += d
    print(json.dumps({"ok": True, "done": done or ["nothing matched"], **extra},
                     ensure_ascii=False, indent=2))


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

    ls = sub.add_parser("lunar-to-solar", help="a lunar-calendar birthday -> solar date (offline)")
    ls.add_argument("year", type=int)
    ls.add_argument("month", type=int)
    ls.add_argument("day", type=int)
    ls.add_argument("--leap", action="store_true", help="the date falls in that year's leap month")
    ls.set_defaults(func=cmd_lunar_to_solar)

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
    fg.add_argument("--entry", default=None, metavar="YYYY-MM-DD",
                    help="delete ONE journal entry from that day (add --nth if it has several)")
    fg.add_argument("--nth", type=int, default=None,
                    help="with --entry: which entry of that day, 1 = the earliest")
    fg.add_argument("--person", default=None, metavar="NAME",
                    help="delete what is stored about one person: their relationship record, "
                         "their name in the journal index, and threads/summary naming them")
    fg.add_argument("--with-entries", action="store_true",
                    help="with --person: also delete the journal entries that mention them")
    fg.add_argument("--relationships", action="store_true",
                    help="delete the whole relationships category and revoke its consent")
    fg.add_argument("--mood", action="store_true",
                    help="delete every stored mood value and revoke mood consent")
    fg.add_argument("--all", action="store_true")
    fg.add_argument("--yes", action="store_true")
    fg.set_defaults(func=cmd_forget)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
