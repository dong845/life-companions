# life-companion: how it works inside

This is the engineering record: the four lenses in detail, the file map, the dependencies, the tests and the honesty gate. None of it is needed to *use* life-companion; [the README](../README.md) covers that.

---

## The four lenses in detail

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
a short read on the day's tone plus a gentle 宜/忌. No star ratings and no invented lucky
numbers: a colour or a number appears only as your chart's traditional 五行
correspondence, labelled as such. It logs the day for you if you want.

**Career fit**
A transparent 21-item interest check grounded in Holland/RIASEC, scored against **188
real O\*NET occupations** (CC BY 4.0). You get a **Low / Moderate / Strong** band and a
confidence note. Never a fake percentage. All 188 carry O\*NET 31.0 interest ratings
and **173** also carry Work Values, so adding a values ranking makes the match genuinely
data-weighted. `--find` maps what you *call* a job
("核磁共振技师", "high school teacher") to an actual occupation code, and labels anything
short of the job itself a weak neighbour, so a role the dataset doesn't hold ("MRI
reconstruction") never comes back as a match. For CVs and cover letters it hands off to
the `job-hunt` skill.

**Relationship reflection**
Attachment theory, Gottman and NVC used as a mirror: separate what happened from the
story about it, voice both sides, name the pattern, and give concrete moves (a repair
phrase, an NVC sentence, the question worth asking them). It tracks people across
incidents so a pattern rests on the actual record rather than on memory. One incident
is never a pattern. Safety comes first: coercive control or violence switches it into
safety mode, which never both-sides abuse and routes to real specialist help.

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
                      trends.py · form_server.py · selfcheck.py
                      _deps.py · _zh.py · _branches.py · _tz.py
data/content/         bazi-interpretation · bazi-life-arc · relationships
                      (the editable interpretation layer — real frameworks)
data/career/          occupations.json (188 real O*NET, CC BY 4.0) · assessment_items.json
data/zh/              OpenCC traditional→simplified table (Apache-2.0): the honesty gate and
                      the crisis scan fold traditional characters before matching
tools/                build_occupations.py: rebuilds data/career/occupations.json from the
                      O*NET databases (for maintainers; the skill never runs it)
                      run_agent_evals.py: runs evals/evals.json through codex, one throwaway
                      project and home each (for maintainers)
evals/                evals.json: the scenarios run_agent_evals.py runs, with their checks
tests/                regression suite (plain unittest, fully offline)
docs/                 internals.md (this page) · internals.zh-CN.md · assets/hero.jpg
```

**Dependencies** — `PyYAML`, `lunar-python` (MIT, BaZi), `pyswisseph` (Western charts),
`sxtwl` (BSD, the optional 立春 cross-check). Scripts try to `pip install` what's
missing; when that fails (no network, PEP 668 externally-managed Python) they print the
exact install command and what degrades without it instead of a traceback.
`python3 scripts/companion.py doctor` reports everything at once.

**Tests** — `python3 tests/test_scripts.py` (a few hundred cases, a minute or two, no network).
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
