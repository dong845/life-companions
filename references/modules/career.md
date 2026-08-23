# Module: Career fit & job-match (工作匹配)

Help the person see what kind of work fits them, how well their current or an
aspiration job matches, and concrete next steps — grounded in real vocational
frameworks, scored honestly with **coarse bands, never fabricated precision**.

**v1 status — shipped.** The scoring engine is live:
`scripts/career_match.py` scores the person against the shipped O\*NET occupation
data (`data/career/occupations.json`, **188 real occupations, CC BY 4.0**) using the
interest-check items in `data/career/assessment_items.json`. **68 of the 188 now
carry real numeric O\*NET Occupational-Interest scores** (1–7, DB 30.3), **62 of
those also carry Work Values** (ipsative rank, DB 30.2) — so the values blend is
now a live, data-backed component, not a hypothetical. The set is densely weighted
toward the user's data-science / ML / research / computing / medical-imaging field,
then spread across all six RIASEC areas. The remaining 120 stay high-point-code-only
(lower confidence). See the **Data reality** note below before you describe a result.

## Computed vs interpretive (the skill's one rule, applied here)
- **Computed (facts).** The RIASEC-6 vector from the person's answers, the
  cosine congruence, the band, the confidence note — all from
  `career_match.py`. Deterministic, offline. **Never hand-score it.**
- **Interpretive (a lens).** What a "Strong on Investigative, softer on your #1
  value" result *means* for them. Always "one way to read this", never a verdict.

## What this module is (and isn't)
- It runs a **transparent, self-built interest check** (Holland/RIASEC), plus an
  **optional** light Big Five read and an **optional** work-values ranking.
- It scores the person's profile against the shipped O\*NET occupation data using
  `career_match.py`, and reports **Low / Moderate / Strong** bands with a
  **confidence note** — no "87% match", no invented salary/demand/percentile.
- Say clearly: the interest check is **grounded in RIASEC (a real framework) but
  is not the O\*NET Interest Profiler or any validated instrument**. Moderately
  informative about interest & satisfaction fit; **does not predict hiring**;
  **not a label**. Show the disclaimer once (disclaimers.md, "Career fit").

## Assessment flow (interests first)
**Preferred: the HTML form.** Run `scripts/form_server.py --form career` (see
`references/forms.md`) — the 21 Likert items + the 6-value ranking are far nicer in a
form than in chat. On submit it writes `career_intake` (`companion.py cache --module
career_intake` → `latest.answers` 0–4 per id + `latest.values_rank` + jobs); feed
that to `career_match.py`. Fall back to the chat flow below only if the user can't use
a browser — an equally valid path, not a downgrade. When using chat, always give
**selectable options** (`AskUserQuestion` where the harness has it, otherwise a
numbered list they answer with numbers) — never free text for a choice.

1. **Interest check first (RIASEC-6).** Present the 21 activity-liking items
   (source: `data/career/assessment_items.json`). Each item is one
   one options question with the 5-point liking scale: Strongly dislike / Dislike /
   Neutral / Like / Strongly like (stored 0–4). You may batch items and lean on
   the journal/profile to pre-fill or skip obvious ones, but keep it honest about
   what was inferred.
   - Frame (verbatim intent): "This is a quick interest check, not an official
     test — built on the real Holland/RIASEC model, but not a validated
     instrument. Answer for what you'd *enjoy*."
   - Short on time? A 12-item subset (2 per type) is a valid run — the confidence
     note will shrink accordingly, and you must say so.
2. **Traits & values only on request** (or when they want a sharper read).
   - Big Five: five pole-description sliders (light read, not a personality test,
     no label, no percentile).
   - Work Values: force-ranked ordering of the six O\*NET values (ipsative).
   - Both optional; interests-only is a complete, valid run.

Score the collected profile with `career_match.py`. Report the **band + the
confidence note together**, and name which components were used and the
**disclosed weights** applied (Interests 0.45 / Values 0.30 / Traits 0.25 when
all present, renormalized over whatever is present).

## Two modes

### Mode A — Current-job congruence coaching
Map the person's RIASEC top types (and, if collected, values/traits) against the
current role's occupation vector from the shipped data.
- Report where the role **fits** (congruent types/values) and where the
  **friction** is — decomposed, e.g. "strong on the Investigative pull the role
  feeds, softer on the Independence you ranked #1".
- This is **congruence coaching, not a verdict.** Offer concrete adjustments
  (craft the role toward the strong types, protect the top value), not
  "quit/stay".

### Mode B — Aspiration-job fit + honest gap sketch

**First, map their words to an actual occupation — don't eyeball it.** They say
「产品经理」/「MRI 重建算法」/"something in UX"; the engine scores against a SOC code.
Guessing that mapping is a wrong answer that looks right, because every downstream
number is then about a job they didn't mean.
```bash
python3 $D/scripts/career_match.py --find "产品经理"        # ranked candidates + data quality
```
Show the top candidates, **confirm with them** («你说的X，我按 O*NET 的「<title>」来算，
行吗?»), and note whether the chosen one carries numeric interests or is code-only —
that sets the confidence you may claim. If it returns **no match**, say the role isn't
in the 188-occupation dataset: ask which shipped occupation is closest in *day-to-day
work* (not job title), or give an interests-only read with **no** occupation congruence
at all. Never substitute the nearest-looking title.

Then, two separate outputs, never merged into one number:
1. **Fit** — congruence band of the person against the aspiration occupation
   vector (same engine), with confidence note.
2. **Gap sketch** — an *honest, qualitative* list of what typically stands
   between them and that role (skills, credentials, experience). The occupation's
   **Job Zone** (in the data) is a useful anchor for typical prep/education, used
   qualitatively — never as a readiness percentage. Framed as "worth pursuing,
   here's a plausible path" or "here's what would need to be true" — never a
   rejection, never a fabricated readiness number.

## Scoring engine (`scripts/career_match.py`)
Person RIASEC-6 vector vs each occupation's RIASEC vector via **normalized cosine
congruence**. Occupations given only a 3-letter high-point code (`riasec: null`)
are expanded **3-2-1** and flagged **lower-confidence**; occupations with full
six-value ratings use `(x-1)/6`. Optional **values** (ipsative cosine, **rescaled** — see below) and
**traits** (soft mean-abs-difference over mapped traits only) blend in with the
disclosed default weights, **renormalized** when components are absent.

The values cosine needed rescaling because an ipsative rank vector cannot point near
the origin: over all 720 orderings its minimum is **0.615**, not 0. Fed straight into
bands built for a [0,1] metric, an **exactly reversed** value ranking still read
"Moderate" — the component could not report a mismatch at all. It is now stretched onto
[0,1] against that verified floor, so opposite rankings read Low. An exhaustive test
pins the floor, so changing the vector definition can't silently skew every score. Output is
a **band** (thresholds 0.55 / 0.75, disclosed as tunable) plus a **confidence
note** that shrinks on short/partial assessments and on code-only occupations.
Raw floats stay internal; the person-facing layer emits **bands + language only**.

**The result comes back as TWO lists, and they are not comparable.** Use
`score_person_grouped()`, which returns `numeric_interests` (68) and `code_only` (120)
ranked and banded separately. For one and the same person the numeric set came out 63%
"Strong" and the code-only set 20% — a shared threshold on differently-shaped
distributions made "Strong" look like one claim when it was two. **Never merge them
into one table, and never say a code-only occupation fits better than a numeric one.**

**An answer set with no shape is refused, not scored.** Cosine ignores magnitude, so
answering the same value to all 21 items produced the vector [k,k,k,k,k,k] — identical
in direction for every k — and still yielded a full ranking, always topped by whichever
occupation sits nearest the uniform direction. `score_person_grouped` now returns
`{"refused": true, …}` when the six type scores barely differ. Say what it says: this is
**"can't measure"**, not "low match". Offer to redo the check, or drop the instrument
and talk about what they've actually done and when they were most absorbed.

**Data reality (be honest about which read you gave):** coverage is now mixed, so
say which kind of match the person actually got:
- **Numeric-interest occupations (68).** Real 1–7 O\*NET interest scores → a genuine
  shape match, `moderate`+ confidence even before values. These cluster in the
  data/computing/health/research space.
- **Work-Values occupations (62 of those 68).** If the person supplied a Work-Values
  ranking, the **values blend engages** (Interests 0.60 / Values 0.40) and lifts
  confidence to `higher`. Naming it is honest — *"this one's scored on both your
  interests and your values"*. The **6 without a Work-Values rating stay
  interest-only** (`work_values:null`, never fabricated) — say so if one lands high;
  don't imply its values were checked.
- **Code-only occupations (120).** Interest signal reconstructed from the 3-letter
  high-point code via the 3-2-1 expansion → **lower confidence, interests only**. The
  values/traits weights can't engage here even if the person supplied them. Call it
  an interest-fit sketch, and push the Work-Values ranking as the real sharpener —
  especially for a single-peaked profile where many roles tie at one band.

Never describe a result as values-weighted when the occupation carried no
`work_values`; the payload's `components_used` / `weights_applied` tell you exactly
which components fired — read them, don't assume. **A values ranking only engages if
all six values are ranked** — an incomplete one (a form where a value was left blank)
degrades silently to interests-only rather than erroring, so if you meant to use
values and `components_used` shows only interests, check the ranking was complete.

**Localization — O\*NET is US labour-market data.** The RIASEC interest-fit itself is
not country-specific and transfers fine (a person's Investigative pull is the same in
Berlin as in Ohio). What does **not** transfer: occupation *titles*, licensing, typical
pay, demand, and whether a role even exists the same way in the person's country. So:
score and interpret **fit** anywhere, but the moment the talk turns to **market,
availability, pay, or credentials in a specific country**, that's a rule-6 fact — verify
it live for *that* country (see the deep-analysis section), don't read it off US data.
When `locale` is `zh`, keep the O\*NET title in English and add a short Chinese gloss;
don't invent a localized occupation name.

```bash
python3 $D/scripts/career_match.py --selftest   # verify the math
python3 $D/scripts/career_match.py --demo        # rank shipped occupations for a sample profile
# In use, import: from career_match import score_person, load_occupations, load_scoring_key
```

## Honesty guardrails
- Bands and qualitative language only — never a precise percentage, salary,
  demand, or percentile.
- Decompose, don't collapse: "strong interest, softer on values" beats one score.
- Always pair a band with its confidence note; say what you don't know.
- The interest check is RIASEC-grounded but **not** the O\*NET Interest Profiler
  or a validated test; never present it as one.
- Not a hiring predictor, not a personality label, not destiny.
- Occupation data ships with its **CC BY 4.0 O\*NET attribution**
  (`data/career/occupations.json`); keep it intact.
- **Machine backstop:** `python3 $D/scripts/selfcheck.py --module career --file draft.md` — exit 1 means a blocker; fix it before sending. Passing is not proof it's honest, only that it's free of the known bad shapes.

## Deep employment / relocation analysis — applying safety.md §1 rule 6

This is the highest-stakes place this module hits **safety.md §1 rule 6 (verify, don't
assume on high-stakes checkable facts)**: a plausible-but-wrong answer on a visa, a
tax ruling, or an employer's hiring status does real harm. When the person asks
about working / relocating to a specific country ("can I stay / work in X", a visa,
job prospects there), a fit reading is **not** enough — the answer turns on
**policy eligibility** and **current market reality**, both changeable, neither
assumable. Run §1 rule 6's research + verify pass (a workflow if available). Its rules,
applied to this domain:

1. **Enumerate EVERY pathway — from the destination authority's own route list,
   not memory.** The best option is often not the first work permit; someone with
   years already in-country may be near PR, which removes sponsor dependency
   entirely. *NL examples (illustrative only — verify for the actual country):*
   each work-permit route, the job-search/orientation-year route, permanent/
   long-term residence, naturalization, EU routes, family routes. Other systems
   have their own primitives — student→work conversion, self-employed/startup/
   entrepreneur visas, points-based/express or regional-nominee programs,
   intra-company transfer, shortage-occupation or global-talent routes. Missing a
   pathway is a real failure.

2. **Test each policy's eligibility conditions against THIS person — don't default
   the answer.** State the conditions, then check each against what you actually
   know; **ask the deciding fact rather than guessing** (§1 rule 6b). *Worked example
   that must never be gotten wrong:* the Dutch **30% ruling requires being recruited
   FROM ABROAD** (a residence/distance test), so a graduate who **studied in-country
   generally does NOT qualify** — even the PhD exception is tested at the moment
   they first came for the PhD. The pivot fact is "were you recruited from outside
   NL, or already resident here?" — elicit it; never collapse to a guess in either
   direction.

3. **Pin the person-specific axes that flip eligibility (ask if unknown).**
   **Nationality** (EU vs non-EU; treaty routes) — and note that some countries,
   **including China, bar dual nationality, so naturalization may require
   renouncing current citizenship**: an irreversible cost, not a clean "pathway."
   Also: current permit/status, arrival/graduation date, **years of continuous
   legal residence and any time spent abroad** (which breaks the PR clock), salary,
   **age** (age-capped schemes), field/shortage-list, and family status. Watch
   **time windows** — e.g. an orientation-year permit must be applied for within a
   fixed window post-graduation; permits expire.

4. **Check the CURRENT status of employers and the market, not their reputation.**
   A good "fit" is not the same as hiring. Check dated, recent signals — hiring
   freezes, layoffs, restructuring, local/domestic-first or non-EU-tightening.
   **If a specific employer's current hiring status can't be confirmed, say so —
   never infer "probably hiring" from reputation or silence.** Present today's
   reality, dated, with the source.

5. **Route to the official / primary source and date it — no bare numbers.**
   IND.nl, Belastingdienst, the government immigration portal (NL examples) — not relocation
   blogs, agency SEO pages, or forums as a sole source. Any threshold, salary
   figure, fee, processing time, or residence-duration must be quoted from a
   fetched official source with its effective year ("as of <date>, may have
   changed; many update every 1 Jan"), or given as "verify at source."
   Adversarially verify eligibility conclusions, thresholds, and employer status
   before presenting.

Deliver honestly, per §1 rule 6. For each realistic pathway make the reasoning
auditable — **conditions | the person's facts (confirmed / needs-confirm /
unknown) | verified against source (source + as-of date) | cost · time · risk ·
reversibility · sponsor-dependency** — then rank by realistic feasibility *and*
whole cost, never legal openness alone. Frame the output as **informational, not
legal or tax advice** (safety.md rule 5); for a binding determination on a
high-stakes move, recommend the official authority or a licensed immigration
lawyer / tax advisor. Never let optimism outrun what you verified. **Attach the
`references/factcheck.md` "来源 · 时效 · 你需自己确认" block — it doesn't ship without it.**

## If they also have a 命盘 read
Both lenses can speak to 事业, and they are not the same kind of claim (SKILL.md, "When
two lenses touch the same question"). Naming where they **rhyme** is worth doing —
「八字那边读出来的『深耕、要有作品感』，跟你兴趣量表上 Investigative 最高，是同一个人的
两种说法」 — it lands precisely because the two came from different places.

What must not happen: the chart **certifying** the fit result, or a career decision
resting on 大运. The occupation data and their own answers own this question; the chart
says what they *want it to feel like*. If they arrive with 「我八字适合做技术，所以…」,
that's safety.md §1 rule 7 — say so, and run the actual assessment.

A clash is information, not a problem to resolve: a symbol read leaning 表达/作品 against
a data read leaning 分析/独处 usually means the pull and the daily work differ — which
is exactly the useful thing to say out loud.

## Hand-off
When the person moves toward a **specific role/application**, route to the
**`job-hunt` skill** (its *apply* mode) for CV building/tailoring and motivation
letters — that skill owns the honest CV work, and also covers discovering and judging
postings and rehearsing interviews. (`job-application` is the older, narrower skill
covering only the apply step; use it if `job-hunt` isn't installed.) This module owns
fit & direction, not CVs.
