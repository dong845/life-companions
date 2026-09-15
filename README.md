# life-companion: computes your chart for real, reads it back as a mirror, and leaves the decision to you

<p align="center">
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-supported-5b5bd6">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-supported-111827">
  <a href="https://clawhub.ai/dong845/skills/life-companion"><img alt="On ClawHub" src="https://img.shields.io/badge/ClawHub-%40dong845%2Flife--companion-7c3aed"></a>
  <a href="https://skillhub.cn/skills/user_f486c577/life-companion"><img alt="On SkillHub" src="https://img.shields.io/badge/SkillHub-life--companion-ff6a00"></a>
</p>

<p align="center">
  <img src="docs/assets/hero.jpg" alt="Four tall cards marked with a tree, a flame, a mountain and waves stand under a thin planetary orbit; an arrow leads to a round mirror that reflects the same cards, softened; beyond it a person holding a notebook stands where a path splits three ways, and no path is marked">
</p>

<p align="center"><sub>Free and open source · your profile and journal stay on your machine · no account, no API key</sub></p>

> **A companion that computes your chart on real calendars and a real ephemeris, remembers what you told it last week, and will not invent a score, a lucky number, or a verdict on your relationship.**

A fortune app will happily give you four stars and a lucky number for the day. life-companion is built to avoid that kind of answer, so it keeps two jobs apart. The **computation** runs in scripts, never in the model's head: 八字 four pillars on real solar-term boundaries, a Western natal chart on the Swiss ephemeris, 紫微斗数, and 188 real O\*NET occupations for career fit. The **reading** is labelled a mirror: one way to see what the chart or the data says, never a forecast. Before a reply goes out, an honesty gate written in code blocks the known ways a reading slides back into fortune-telling.

It is a skill for [Claude Code](https://claude.ai/code) and [Codex](https://openai.com/codex), and it runs under any agent that can execute Python and read files. You just talk to it. It keeps a profile and a journal in `~/.companion/` on your own machine, asks before it stores anything sensitive, and follows up on what you said you'd do. Every computation runs offline.

<p align="center">
  <a href="#start-here"><strong>Start here</strong></a> ·
  <a href="#what-you-get"><strong>What you get</strong></a> ·
  <a href="#what-makes-it-different"><strong>What's different</strong></a> ·
  <a href="#what-it-will-ask-you"><strong>What it asks</strong></a> ·
  <a href="#quick-start"><strong>Quick start</strong></a> ·
  <a href="#troubleshooting"><strong>Troubleshooting</strong></a>
</p>

---

<a id="start-here"></a>

## Start here

You don't pick a module. Say what's on your mind and the lens follows:

| You want | Lens | What you get |
| --- | --- | --- |
| Your chart read | **Destiny**: 八字, Western natal, 紫微斗数, 合婚 | The computed chart, then a reading in plain words, from one sentence down to a decade-by-decade timeline |
| How today looks, or to write the day down | **Daily reading + journal** | A short card for the day, woven with what you actually wrote, and the day logged if you want |
| To think about work | **Career fit** | A 21-item interest check scored against 188 real O\*NET occupations, as Low / Moderate / Strong bands, never a percentage |
| To make sense of a relationship | **Relationship reflection** | What happened, kept apart from the story about it; both sides; a pattern only when the record shows one; and a concrete next move |
| Just to talk | **Journal + memory** | Someone who remembers the last few weeks and follows up on one thing, gently |

Once it is [installed](#install), that looks like this:

```text
Use life-companion — read my BaZi: 12 April 1993, 7:35am, male, born in Beijing.
Use life-companion — how does today look? And log it: rough day, mood 4/10.
Use life-companion — I'm not sure what kind of work suits me.
Use life-companion — we had a fight last night. Help me think it through.
```

It answers what you asked first. No lens needs a profile to compute, so the questions about you come after the answer, as a short list you pick from.

---

<a id="what-you-get"></a>

## What you get

Two kinds of output, kept visibly apart:

| | What it is | Where it comes from |
| --- | --- | --- |
| **Computed** | Pillars, 大运, planet positions, 紫微 palaces, career bands, mood trends | Scripts: reproducible and offline. The model never works a chart out by hand |
| **Read** | What any of it might mean for you | A labelled mirror: one way to see it, never a forecast |

**What is in a reading.** The computed facts first, then the reading in everyday words, with each term glossed the first time it appears. One disclaimer at the top, then plain sentences instead of a hedge in every clause. When two systems disagree, say the day's 八字 leans one way and its 生肖 relation another, the reading says so rather than averaging them into one verdict.

**What is *not* in it.** No star ratings, and no invented lucky numbers: a colour or a number appears only as your chart's traditional 五行 correspondence, labelled as that. No percentage on a career fit. No 合/不合 verdict, score or recommendation for a couple. No "you will", no illness, breakup or ruin predictions, and no medical, financial or legal advice. And when a message carries a crisis, no chart at all: real, local help instead.

**What stays on your machine.** A profile, a consent record, a journal with one Markdown file a month, and a small state folder for cached charts and follow-ups, all in `~/.companion/`. That folder sits outside the skill's own, so no install or update ever touches it. [Details below](#your-data-and-privacy).

---

## What makes it different

Plenty of tools will tell your fortune. The difference is what happens to the claims:

- 🧮 **Computed by scripts, never by hand** — 八字 on real 節氣 boundaries, tested pillar by pillar against an independent calendar; a Western chart on the Swiss ephemeris; career data rebuilt from the O\*NET 31.0 database. With no birth time there is no Ascendant and no 紫微 chart, rather than guessed ones.
- 🪞 **A mirror, not a forecast** — every reading is labelled one way to see it. It never says what will happen, and a real decision about a job, a partner or money goes to the lens that owns it, not to a reading.
- 🚦 **Honesty rules are code, not good intentions** — before a reply goes out, a gate blocks fabricated precision in digits or in words, fatalism including the hedged kind ("大概率保不住"), clinical labels on someone who isn't there, and an invented helpline number.
- 🧠 **It remembers, and follows through** — your profile, your journal and the threads you left open carry across sessions, and it follows up on one thing you meant to do, without nagging.
- 🔒 **Local, and yours** — birth data, relationship notes and moods are each allowed separately, and a write without consent is refused in code. `forget` shows exactly what it will delete, then removes it from every copy.
- 🆘 **A crisis drops the fortune-telling** — no chart and no "the stars say it will pass": real crisis lines for where you are, and findahelpline.com when it doesn't know where that is. It never guesses a number.

Three of those, from what testing and real runs turned up:

**The chart that read the wrong clock.** A birth outside Beijing time is read on two clocks: the year and month pillars from the solar-term instant, the day and hour pillars from the local clock. An earlier version swapped only the pillars it displayed, and everything derived from them kept reading the other chart. An Amsterdam birth just after 立春 came back with the wrong 生肖 and a 大运 running the wrong way, and over 96 sampled birth hours the year and month 十神 were wrong in 26 for Amsterdam, 50 for New York and 10 for Sydney. Everything now derives from the pillars on the page, and the test suite checks all four pillars against an independent calendar at six UTC offsets.

**The sentence that walked past the gate in traditional characters.** Every pattern in the honesty gate was written in simplified Chinese. 「你命中註定會離婚」 ("you are fated to divorce") passed while its simplified twin was blocked, and 「我想自殺」 ("I want to kill myself") raised no crisis flag at all. Text is now folded to simplified characters before both checks, one character for one character, and Cantonese wording has patterns of its own.

**The delete that happened on the spot.** In a Codex run, 「把关于小李的记录都删掉吧」 ("delete everything about Xiao Li") was carried out at once, although the docs said to confirm first. The rule lived only in a reference file, and the agent skipped it. So the check moved into the command: every `forget` now runs the real deletion on a copy of your data, shows what would go, and deletes only when it runs again with `--yes`.

---

## What it will ask you

Only what the thing you asked for needs, and after the answer rather than before it:

1. **What to call you, your language, where you are, and a tone** — offered once, after the first answer, as options. Where you are sets daily timing and decides which country's crisis line you would be given.
2. **Birth details, for a chart or a daily card** — and first, whether to keep them: *keep them on this machine* · *just compute this once* · *don't use them*. Then the date, in the solar or the lunar calendar (a lunar date is converted by the script, never by hand); the time, as exact, rough or unknown; the birthplace; and which way the 大运 should run, a setting the traditional rule needs rather than a statement about who you are.
3. **21 quick interest items, and an optional values ranking** — only when you ask about work.
4. **Consent for relationship notes** — only when you bring a relationship up.

It prefers options you pick over questions you type, and when a browser is handy it can offer a local form instead of the chat.

**How far it goes on what it knows.**

| It knows | It will give you |
| --- | --- |
| A birth date | The chart without its hour pillar, and no 紫微 chart or Ascendant rather than guessed ones |
| …plus a rough birth time | The chart, and which pillars that uncertainty could move |
| …plus the exact time and birthplace | All four pillars, True Solar Time if you want it, the 紫微 chart and the Ascendant |

---

## Quick start

### Install

Needs **Python 3.9+**. Nothing to install by hand: on first use the scripts fetch `PyYAML`, `lunar-python`, `pyswisseph` and `sxtwl`, and if they can't (no network, or a PEP 668 system Python) they print the exact command instead. Pick one of four paths.

**Option 1 — one line with [`npx skills`](https://github.com/vercel-labs/skills)** (simplest):

```bash
npx skills add dong845/life-companions
```

It prompts for the agent and scope; `-g` installs globally, `-a claude-code` / `-a codex` skips the prompt, `-y` runs non-interactively. The repository root *is* the skill, so the whole folder is copied into your skills directory.

**Option 2 — as a Claude Code plugin** (managed updates):

```text
/plugin marketplace add dong845/life-companions
/plugin install life-companion@life-companion
/reload-plugins
```

Invoked as `/life-companion:life-companion`. Remove any manual copy in `~/.claude/skills/` or you will see the skill twice. Third-party marketplaces don't auto-update: for a new release, run `claude plugin marketplace update life-companion`, then `claude plugin update life-companion@life-companion`, and restart.

**Option 3 — clone** (best if you want to edit it; changes take effect immediately, which the plugin cache doesn't give you):

```bash
git clone --depth 1 https://github.com/dong845/life-companions.git ~/.claude/skills/life-companion
```

**Option 4 — from [ClawHub](https://clawhub.ai/dong845/skills/life-companion)**, the skill marketplace for [OpenClaw](https://clawhub.ai) agents:

```bash
openclaw skills install @dong845/life-companion
```

life-companion is also listed on **[SkillHub](https://skillhub.cn/skills/user_f486c577/life-companion)**, a Chinese-language skills community, which is handy for browsing and comparing; installation still goes through one of the paths above.

Whichever you pick, your data doesn't come with it: the profile and journal live in `~/.companion/`, and no install or update touches them. To check the setup, run `python3 scripts/companion.py doctor` from the skill's folder.

### Use it

Just talk. "帮我看看八字" or "how does today look?" is enough to trigger it; in Claude Code, `/life-companion` works too.

For onboarding or the 21-item career check, it may offer a local form instead of the chat:

```bash
python3 scripts/form_server.py --form onboarding    # or: --form career
```

It prints a `http://127.0.0.1:<port>/?token=…` link and opens it. The page opens only with the link it printed, saves one submission, and stops itself on submit or after 15 minutes. The chat does the same job just as well; you never have to use the form.

---

<a id="your-data-and-privacy"></a>

## Your data and privacy

```
~/.companion/          chmod 700: on macOS and Linux only you can read it
├── profile.yaml       who you are
├── consent.yaml       what you allowed
├── journal/           your entries: one Markdown file a month, plus index.jsonl
└── state/             cached charts, working memory, per-person tracking
```

**Consent is per category, and revocable.** Birth data, relationship details and mood history are each granted separately. Without consent they aren't collected, inferred or stored, and a write without it is refused in code. Revoking is not deleting: every script stops reading that category at once, the files stay, and you are told what is still stored and how to delete it, so a mistaken revoke loses nothing.

**Deletion reaches every copy.** Just ask. It removes the matching content from the journal, its index, the relationship log, the working memory and the caches, not only the one obvious file:

| Say | Runs |
| --- | --- |
| "delete my birth data" | `companion.py forget --birth` |
| "forget June" | `companion.py forget --month 2026-06` |
| "delete what I just logged" | `companion.py forget --entry 2026-06-18 --nth 2` |
| "delete everything about him" | `companion.py forget --person Sam --with-entries` |
| "stop keeping relationship notes" | `companion.py forget --relationships` |
| "delete my mood scores" | `companion.py forget --mood` |
| "wipe everything" | `companion.py forget --all` |

Run like that, each one only lists what it would remove and changes nothing. It deletes once you confirm and it runs again with `--yes`.

**Offline, with one exception stated precisely.** Charts, readings and career matching make no network calls, so your data never leaves the machine and nothing costs API credit. The *first* run may `pip install` the four dependencies. That is a package download, not your data leaving, but it is a network call, so it is named here. Set `LIFE_COMPANION_NO_AUTOINSTALL=1` to forbid it, and the scripts print the install command instead.

**A crisis in a first message is never stored.** With no profile yet, nothing is kept at all. If you already keep a journal here, a crisis entry is logged only with a plain line telling you so, and how to delete it.

---

<a id="troubleshooting"></a>

## Troubleshooting

**`doctor` says a dependency is missing.** The scripts tried to install it and couldn't. `doctor` names each package with its install command and what stops working without it: `pyswisseph` is needed only for the Western chart, and `sxtwl` only for the 立春 cross-check. On a PEP 668 "externally managed" Python, install into a venv, or use `pip install --user`, `pipx` or your OS package manager.

**The skill shows up twice.** It is installed as a plugin and also kept as a copy in `~/.claude/skills/`. Nothing deduplicates them, so remove one.

**`forget` didn't delete anything.** That was the preview. It lists what would go and changes nothing; run the command it hands back, which ends in `--yes`.

**It won't save my birth details.** Birth data needs consent first. Without it, `set-profile` refuses with exit code 3 and writes nothing. A chart can still be computed for this conversation without storing anything.

**The form page says 打不开.** Open the whole link the server printed, the one ending in `?token=…`. A bare `http://127.0.0.1:8760/` is refused on purpose.

**The form server won't start.** An earlier one is probably still running. It tries the next port on its own, and if ten in a row are taken it says so: run `pkill -f form_server.py`, or just answer in the chat.

**On Windows.** Use `python` (or `py -3`) instead of `python3`. `~/.companion` can't be locked down with `chmod 700` there, so "only you can read it" holds on macOS and Linux only.

---

## Security

The optional forms are served by a temporary HTTP server bound to `127.0.0.1` only, which stops itself on submit or after 15 minutes. The page it serves shows what is already stored, birth data included, and a submission writes to your profile and consent record, so binding to loopback is not the only barrier. The server mints a one-time token and prints it in the link. The page opens only with that token and under the host the server bound, which also stops a page on a hostile name that resolves to 127.0.0.1. A submission needs the same token and, when the browser sends one, the page's own origin, and a lock saves exactly one unless the server was started with `--keep-alive`. Anything else gets a 403 and writes nothing.

The honest residual limit: any process running as **you** on **your** machine can read the token from the terminal, so this defends against a hostile web page, not against local malware already running under your account.

`forget --all` deletes a folder only if it holds the README that `init` writes, so a mistyped `COMPANION_HOME` can't take an unrelated directory with it. The skill uses no API keys and never gives medical, financial or legal advice.

---

## How it works inside

The four lenses in detail, the file map, dependencies, tests, and how to run the honesty gate on a draft: **[docs/internals.md](docs/internals.md)**. You don't need it to use life-companion.

---

## License

MIT, see [LICENSE](LICENSE). Occupation data is from the O\*NET Resource Center (U.S. Department of Labor), used under **CC BY 4.0**; the attribution ships inside `data/career/occupations.json` and must stay there.

---

*The computation is fact. The reading is a mirror. You're the one who decides.*
