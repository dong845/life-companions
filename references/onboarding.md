# Onboarding — first run

Goal: collect the **minimum** to be useful *for what they actually asked*, warmly
and with consent — not a 20-question intake. Onboarding is **tiered and lazy**:
gather Tier 0 once, then only the tier the current request needs. It's resumable
(`onboarding_complete` / explicit `null`s mean "not asked yet", so nothing gets
re-asked). **Never** run onboarding during a crisis — help first (see safety.md).

**Preferred method: the HTML form.** Run `scripts/form_server.py --form onboarding`
(see `references/forms.md`) — it collects Tier 0 + the consent-gated birth block in
one clean page and writes it for you. The tiered chat flow below is the **fallback**
when the user can't open a browser or would rather just talk. Either way the tiers
and consent rules are the same.

When you do use chat: give **selectable options** wherever there's a choice — this
person strongly prefers picking over typing. Use `AskUserQuestion` if your harness has
it; otherwise a short **numbered list** they answer with numbers. Keep free text for
names and dates only.

## Tier 0 — identity (always, ~4 quick things)
Collect once, up front, before any reading:
- **What to call you** (free text).
- **Language** → sets `identity.locale` (offer 中文 / English / 双语). Ask in the
  language they wrote to you in.
- **Where you are** (city/country or timezone) → `identity.timezone`. Needed for
  correct daily timing *and* for localizing crisis helplines — you can say that.
  Take their own words and resolve them offline:
  `companion.py resolve-tz "柏林"` → `Europe/Berlin`. One clear candidate: store it.
  Several: ask which. **None: ask for a nearby major city — never guess a zone**, since
  it decides which country's crisis line they'd be given. Keep the raw words in
  `identity.location` too.
- **Tone** you want (options: 温暖直接 / 轻松俏皮 / 简洁克制) → `preferences.tone`.

Write with `companion.py set-profile --merge-json '{"identity":{…},"preferences":{…}}'`.

## Tier 1 — birth block (only for destiny / daily-fortune)
First **ask consent**: birth data is sensitive; it stays on this machine.
`companion.py consent --set birth=yes` (if no → skip these modules gracefully).
Then collect:
- **Birth date** (solar/公历 YYYY-MM-DD) — required for BaZi.
- **Birth time** (HH:MM) — offer "know it exactly / roughly / don't know". If
  unknown, that's fine: BaZi still works; Western rising/houses won't. Store
  `time_known` explicitly so it's never re-asked.
- **Birthplace** (city) — store the name in `birth.place`, **and immediately derive
  and store `birth.lat`, `birth.lon`, and `birth.tz_at_birth`** from it. A city's
  coordinates and its timezone are stable public reference facts (not fabrication, not
  prediction — this is a lookup, like knowing Beijing is ~39.9°N/116.4°E in
  Asia/Shanghai), so fill them rather than leaving them null.
  **Store `tz_at_birth` as an IANA zone NAME** (`Asia/Shanghai`, `Europe/Amsterdam`),
  not as a number of hours. Pass that name straight to `astro.py --tz` and it resolves
  the offset actually in force at that birth moment — historical DST and zone changes
  included. Do **not** work the offset out yourself; that turns a lookup into a guess,
  and a summer European birth is the case people get wrong. (A plain offset like `8`
  or `-5` still works if a zone name genuinely isn't determinable.)
  Without these three, **the full Western natal chart can never compute the
  Ascendant/houses and BaZi can't offer True Solar Time** — so this is the step that
  makes those features actually work.
- **Chart convention** (the field is `birth.gender`) — BaZi's 大运 runs forward or
  backward by the rule 阳男阴女顺行 / 阴男阳女逆行, so the calculation needs one of two
  values and the tradition offers no third. **Ask for it as what it is: a setting the
  chart needs, not a statement about who they are.** 「起大运的方向在传统规则里只有两种
  取法，我按哪一种给你算？」 If they are non-binary, say plainly that the tradition has
  no convention for it, let them pick which one to use, and note that the choice is
  theirs and reversible. Their actual identity belongs in `identity.pronouns`, which is
  never inferred from this field. Do not let a chart setting become a label.

Write to `birth:` via `set-profile`. Then hand to `modules/destiny.md`.

## Tier 2 — career (only when they use the career module)
Don't front-load. The career module runs its own short assessment (interest items
first). See `modules/career.md`.

## Tier 3 — relationships (only when they bring it up)
Consent first (`relationships=yes`). Mostly accrues from journaling — no intake
quiz. See `modules/relationships.md`.

## Finishing
When Tier 0 (+ whatever tier the request needed) is in, mark onboarding done —
`companion.py set-profile --merge-json '{"onboarding_complete": true}'` — and go
straight to fulfilling the original request —
don't make them re-ask. A good first run ends with the thing they came for
(their chart, their first logged day), not a form.

## Example (first message = "帮我看看八字")
1. `status` → not initialized → `init`.
2. Tier 0 (4 options-based questions) → `set-profile`.
3. Consent birth → Tier 1 birth block → `set-profile`.
4. Straight into `modules/destiny.md` → compute → deliver their 命盘. One
   disclaimer note at the top (from disclaimers.md), then the reading.
