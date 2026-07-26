# HTML forms — the nice way to collect input

For anything with several fields (onboarding, a birth block, the 21-item career
check), a styled local form beats asking one question at a time in chat. It's
clearer for the user, faster, and it writes straight to their private home.

`scripts/form_server.py` serves a small localhost page, opens the browser, and on
submit writes the result via `companion.py` (atomic + consent-gated) and drops a
`~/.companion/.form_result.json` marker. Nothing leaves the machine.

## When to use it
- **Onboarding** (first run): the default. Only fall back to chat questions if the
  user can't open a browser or prefers to just talk.
- **Career assessment**: strongly preferred — 21 Likert items + a values ranking is
  painful in chat, pleasant in a form.
- Short, single-answer things (a mood, one birthday, a yes/no) stay in chat — a form
  is overkill.

## How to run it
Launch it **in the background**, tell the user to fill the page, then read the
result once they submit:

```bash
D=<this-skill-dir>
# onboarding (pre-fills anything already in the profile):
python3 $D/scripts/form_server.py --form onboarding &
# career assessment:
python3 $D/scripts/form_server.py --form career &
```
It prints `SERVING http://127.0.0.1:8760/` and opens the browser. On submit it
prints `SUBMITTED {...}` and writes the data. Give the user the URL too, in case the
browser didn't auto-open.

## Knowing they're done
Two signals — poll whichever is easy:
- `~/.companion/.form_result.json` appears/updates with `{"status": …}`.
- For onboarding: `companion.py status` flips `onboarding_complete:true`.
- For career: `companion.py cache --module career_intake` has a `latest` block with
  `answers` (0–4 per item id), `values_rank`, and the jobs — feed that to
  `career_match.py` to score (bands only, no fake %).

When the user says they've submitted (or the marker appears), read the profile /
intake, confirm warmly in one line, and continue with what they came for. Then
**stop the server** (kill the background job) — don't leave it running.

## Design note
The forms are meant to feel like a private ledger, not a survey: paper/lamplight
(follows the OS theme), 朱砂 as the one accent, 五行 dots as section marks, a
consent *lock* on the birth block, a 印章 stamp on save. If you extend them, keep
that restraint — one accent, lots of calm. The honesty line stays on every form:
*算出来的是事实，读出来的是镜子；数据只在你本机。*

## Adding a new form
Add a `render_<name>()` + `write_<name>()` pair in `form_server.py` and a
`--form <name>` branch. Reuse the shared CSS + `page()`/`eyebrow()`/`opt()` helpers
so it stays visually one family.
