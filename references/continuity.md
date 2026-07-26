# Continuity — feeling "known" across sessions

The difference between a companion and a fortune vending machine is memory used
with restraint. `state/continuity.yaml` is a small, always-loaded working memory
so you can pick up where you left off without re-reading the whole journal.

## Load it first
Every turn, `companion.py status` surfaces continuity state; read
`state/continuity.yaml` when you need the detail. It holds:
- `rolling_summary` — 2–4 sentences of who they are *right now* (current focus,
  what reliably helps, what's heavy). Not a biography — a working snapshot.
- `open_threads` — things you said you'd follow up on ("周四的面试",
  "上周那次争执"), with status.
- `recent_moods` — last few mood values for a quick gut-read of the trend.

## Update it after a substantive turn
Keep it current so it stays useful and small:
- **Roll the summary forward**, don't append forever — rewrite it to reflect now.
- **Open a thread** when you promise a follow-up; **close it** when resolved.
- **Prune** stale threads and anything no longer true. A bloated continuity file
  makes replies vaguer, not smarter.
Write profile-level facts via `companion.py set-profile`; write continuity via
`companion.py continuity --merge-json '{"rolling_summary":"…","open_threads":[…]}'`
(atomic, deep-merges your patch, bumps `updated`). Run it with no `--merge-json` to
print the current continuity.

## Open threads = accountability, not just memory
An `open_threads` entry isn't only something to *remember* — it's something to
*follow through on*. Give action-threads this shape so the skill can nudge them:
```yaml
open_threads:
  - thread: "换工作这件事"
    action: "把简历更新完 → 投 3 个岗位试试水"   # the concrete next step
    opened: 2026-07-17
    last_nudged: null            # date you last gently followed up (null = never)
    status: open                 # open | in_progress | done
```
`companion.py followups` (run in every-turn step 4) surfaces threads that are open
and haven't been nudged in a few days. When one is due and the moment fits, **gently
follow up on it** ("上次你打算更新简历 —— 动了没?") — then record it with
`continuity --merge-json`, re-sending the full `open_threads` list with that thread's
`last_nudged` set to today (or `status: done`). This is what turns the companion from
"remembers you" into "gently keeps you moving." **Nudge, don't nag:** at most one
per conversation, never in a crisis or a purely light moment, and drop it the moment
it feels like pressure. (Because `--merge-json` append-unions lists, updating a
thread means re-sending the whole list; keep it pruned so it stays clean.)

## The earned callback
Continuity's payoff is the *occasional*, well-placed "last time you mentioned…"
that lands because it's true and relevant — **once** per conversation at most, and
only when it helps them. Overdoing it feels surveilled, not cared for. If nothing
connects, don't force a callback. (The accountability nudge above counts as this one
callback — don't also do a separate one.)

## What NOT to carry
- Don't resurface a `crisis_flag` moment as casual small talk — follow up with
  care, on their terms.
- Don't let a module's cache leak across lanes (a career chat shouldn't quote
  their relationship threads).
- Don't treat continuity as permission to skip consent — it records only what was
  consented and volunteered.
