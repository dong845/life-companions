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

When you do use chat: use **AskUserQuestion selectable options** wherever there's a
choice (this user strongly prefers picking over typing); keep free-text for names
and dates only.

## Tier 0 — identity (always, ~4 quick things)
Collect once, up front, before any reading:
- **What to call you** (free text).
- **Language** → sets `identity.locale` (offer 中文 / English / 双语). Ask in the
  language they wrote to you in.
- **Where you are** (city/country or timezone) → `identity.timezone`. Needed for
  correct daily timing *and* for localizing crisis helplines — you can say that.
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
- **Birthplace** (city) — for longitude (True Solar Time toggle) and any Western
  chart. Approximate is OK.
- **Gender** (male/female) — needed for BaZi 大运 direction (阳男阴女顺行…). Ask
  plainly; store `birth.gender`.

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
