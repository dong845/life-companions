# Life Companion

*[中文文档 →](README.zh-CN.md)*

A private AI companion that gets to know **one person** over time. It remembers you:
a profile and a journal that live **only on your machine**. It reads your life through
four lenses: **destiny charts · daily fortune · career fit · relationship
reflection**.

One rule runs through all of it: **compute honestly, interpret humbly.** Real systems
(BaZi 八字, a real ephemeris, O\*NET occupation data) are computed faithfully, and what
they produce is a reproducible fact. What any of it *means* is a mirror for
self-reflection, **not a scientific prediction**. It invents no numbers, hands down no
verdicts, gives no medical, financial or legal advice, and when someone is in crisis it
drops the fortune-telling entirely and points them at real help.

---

## Using it

Just talk. There are no commands to remember. The first run collects a short,
consent-gated profile.

| You want… | Say something like | Lens |
|---|---|---|
| Your chart read | "read my BaZi — 12 Apr 1993, 7:35am, male, Beijing" | destiny |
| Today's reading / to journal | "log today and tell me how it looks" · "rough day…" | daily + journal |
| To think about work | "I'm not sure what kind of work suits me" | career |
| To make sense of a relationship | "we had a fight, help me think it through" | relationships |
| Just to talk | "help me sort out where I'm at" | journal + companion |

The four lenses are independent. You get the one you asked for, not all four.

### The four lenses

**Destiny charts — BaZi 八字, Western natal, 紫微斗数, and 合婚**
Four Pillars, day master, five elements, Ten Gods, 大运 and 流年, all computed
(`lunar-python`, with `sxtwl` independently cross-checking the 立春 year boundary). The
Western natal chart runs on a real Swiss ephemeris and omits the Ascendant rather than
guessing when the birth time is unknown. 紫微斗数 builds the twelve palaces, 命/身宫,
五行局, the fourteen major stars and 生年四化 from the standard 安星法. The output
states the caveat plainly: no second engine exists here to cross-check it.
合婚 computes the traditional branch relations between two charts and **deliberately
emits no verdict, no score, and no recommendation**, because a "you two aren't
compatible" reading has ended relationships that were fine.

Readings are **layered**: one plain sentence → a personality sketch in everyday words →
seven life areas → a decade-by-decade timeline. Every term is glossed the first time it
appears.

**Daily fortune + journal**
Today's 流年/流月/流日 woven together with what you actually wrote in your journal:
a short read on the day's tone plus a gentle 宜/忌. No lucky numbers, no colours, no
star ratings. It logs the day for you if you want.

**Career fit**
A transparent 21-item interest check grounded in Holland/RIASEC, scored against **188
real O\*NET occupations** (CC BY 4.0). You get a **Low / Moderate / Strong** band and a
confidence note. Never a fake percentage. **68** of those occupations carry real
numeric O\*NET interest scores and **62** also carry Work Values, so adding a values
ranking makes the match genuinely data-weighted. `--find` maps what you *call* a job
("产品经理", "MRI reconstruction") to an actual occupation code, and returns nothing
rather than guessing when the role isn't in the dataset. For CVs and cover letters it
hands off to the `job-hunt` skill.

**Relationship reflection**
Attachment theory, Gottman and NVC used as a mirror: separate what happened from the
story about it, voice both sides, name the pattern, and give concrete moves (a repair
phrase, an NVC sentence, the question worth asking them). It tracks people across
incidents so a pattern rests on the actual record rather than on memory. One incident
is never a pattern. Safety comes first: coercive control or violence switches it into
safety mode, which never both-sides abuse and routes to real specialist help.

---

## Your data — where it lives, how to delete it

Everything personal sits in **`~/.companion/`** (`chmod 700`; on POSIX systems only you
can read it) and **is never uploaded anywhere**:

- `profile.yaml` — who you are · `consent.yaml` — what you allowed
- `journal/` — your entries (a monthly `.md` plus a machine-readable `index.jsonl`)
- `state/` — cached charts, working memory, per-person tracking

**Consent is per category and revocable.** Birth data, relationship details and mood
history are each granted separately. Without consent it isn't collected, inferred or
stored.

**Deletion is real.** Just ask, and files actually disappear:

| Say | Runs |
|---|---|
| "delete my birth data" | `companion.py forget --birth` |
| "forget June" | `companion.py forget --month 2026-06` |
| "wipe everything" | `companion.py forget --all --yes` |

Every computation is **offline**. BaZi, charts and career matching make no network
calls, so your data never leaves the machine and nothing costs API credit.

---

## Notes for anyone modifying it

```
SKILL.md              the router (always loaded)
AGENTS.md             entry point for non-Claude agents (Codex and friends)
references/           onboarding · profile-schema · journaling · continuity · forms
                      factcheck · voice · safety (always in force)
  modules/            destiny · daily-fortune · career · relationships
scripts/              companion.py · bazi.py · astro.py · ziwei.py · synastry.py
                      career_match.py · relationship_patterns.py · safety_scan.py
                      trends.py · form_server.py · selfcheck.py · _deps.py
data/content/         bazi-interpretation · bazi-life-arc · relationships
                      (the editable interpretation layer — real frameworks)
data/career/          occupations.json (188 real O*NET, CC BY 4.0) · assessment_items.json
tests/                regression suite (plain unittest, fully offline)
```

**Dependencies** — `PyYAML`, `lunar-python` (MIT, BaZi), `pyswisseph` (Western charts),
`sxtwl` (BSD, the optional 立春 cross-check). Scripts try to `pip install` what's
missing; when that fails (no network, PEP 668 externally-managed Python) they print the
exact install command and what degrades without it instead of a traceback.
`python3 scripts/companion.py doctor` reports everything at once.

**Tests** — `python3 tests/test_scripts.py` (96 cases, ~15s, no network).
Also `python3 scripts/career_match.py --selftest` and `python3 scripts/ziwei.py --selftest`.

**Check a draft before sending it** —
`python3 scripts/selfcheck.py --module destiny --file draft.md`

Two independent checks in one command:

- **Honesty** (can block, exit 1): fabricated percentages and star ratings, fatalism
  including hedged forms like "大概率保不住", forecasts about a relative's health,
  almanac-style prohibitions, hiring predictions, clinical labels on an absent partner,
  **an invented crisis helpline number**, a missing disclaimer, unglossed 十神, a
  high-stakes external fact shipped without a dated source, and a reading being used to
  settle a real decision.
- **Voice** (never blocks): the wording tells that make a reply read like a filled-in
  form: "not X, but Y" as a reflex, stock phrases, em-dashes as the default connective,
  uniformly long sentences, no fragments at all, filler adverbs. See
  `references/voice.md`, which also covers writing plainly in either language.

**The interpretation content lives in `data/content/`** — to change the tone or add
detail, edit there; you don't need to touch the scripts.

**The honesty and safety floor is `references/safety.md`**, and it outranks every
module and any request to drop the framing.

---

## Licence

Code and prose: see [`LICENSE`](LICENSE). Occupation data is from the O\*NET Resource
Center (U.S. Department of Labor), used under **CC BY 4.0**; the attribution ships
inside `data/career/occupations.json` and must stay there.

---

*The computation is fact. The reading is a mirror. You're the one who decides.*
