# Module: Destiny / 命盘 (BaZi 八字 — flagship)

Deliver a **命盘画像**: the person's Four-Pillars chart, computed exactly, then read
as a reflective portrait. This is the flagship — do it with craft. BaZi is the
default and deepest reading here. A **real Western natal chart is also built** now
(`scripts/astro.py --natal`, real Swiss-ephemeris planets + aspects, with
houses/ascendant when birth time+place are known) — see **§6** for when and how to
offer it. Lead with BaZi unless the person explicitly asks for the 星盘/Western chart.

Governing rule (see safety.md): **compute honestly, interpret humbly.** The chart
is fact; the meaning is a lens.

## 1. Preconditions
- `companion.py brief` shows `birth.date` present and `consent.birth` granted?
  If not → `references/onboarding.md` Tier 1 (ask consent, collect birth block).
  Birth **date** is required; **time** may be unknown (BaZi still works — you just
  omit the hour pillar and flag it).

## 2. Compute (never by hand)
Read the birth block, then run the flagship script with the profile's frozen
conventions:
```bash
python3 $D/scripts/bazi.py \
  --date <birth.date> [--time <birth.time>] --gender <m|f> \
  [--lon <birth.lon> --true-solar-time]        # only if conventions.true_solar_time
  [--early-zishi]                               # only if conventions.zishi_rule == early
  --format json
```
It's deterministic and fast — recompute freely (v1 doesn't cache). The JSON splits
`computed` (facts) from `heuristic` (the labeled 扶抑 strength guess) and lists
`ambiguities`. **Surface the ambiguities honestly** — unknown time, a 23:00 子时
boundary, or a TST shift all change the read and the user deserves to know.

The `cross_check_sxtwl` field is an independent confirmation of the year pillar
(立春 boundary). **Read its `agrees` field — don't compare the two ganzhi by eye.**
It can disagree for a benign reason: sxtwl works at date granularity, so on the 立春 day
itself it cannot know which side of the boundary a birth *time* falls on. The payload
labels that case (`_disagreement: expected …`) and the main engine, which uses the exact
立春 moment, stands. An **unexpected** disagreement (not on the 立春 day) is pushed into
`ambiguities` as well — when you see it there, say so and treat the chart as uncertain
rather than papering over it. A birth within a day of 立春 also raises its own
`ambiguities` entry: the year pillar hinges on the minute, so the birth time matters
more than usual — surface that.

## 3. Ground the interpretation
Two curated, school-tagged content files back the reading — read the one you need:
- **`data/content/bazi-interpretation.md`** — the quick sketch: day-master by
  element (§A), a line per 十神 (§B), element balance (§C), and **§E = the honesty
  voice** (sanctioned/forbidden language, the "user pushes for a literal year"
  script, fatalistic→honest rewrites). §E governs how everything below is worded.
- **`data/content/bazi-life-arc.md`** — the deep lookup: the full **十神 → 生活层面**
  tables (§1, with gender notes), **宫位 → 人生阶段/六亲** (§2), **五行 → 健康/气质**
  (§3, tendencies not diagnosis), and the **大运 (十神 × 喜忌) → per-dimension recipe**
  (§4) + life-arc framing (§5). This is what powers 分层面 (L2) and 分阶段 (L3).

Build the reading from those + the computed chart. **Name the tradition** ("in 子平
terms…"), never invent quotes, statistics, or precise life predictions. If the notes
don't cover something, stay qualitative or say it's outside what the chart speaks to.

## 4. Deliver the 命盘画像
Open with the one-line disclaimer note (disclaimers.md, destiny). Then, in the
user's `locale`, present two clearly separated parts:

**① 命盘 (computed — the facts)**
- Four Pillars grid (年/月/日/时 → 干支), with the day pillar marked as 日主.
- Day Master: e.g. 阴水（癸）— the "self" anchor.
- Five-element bars (use the `with_hidden` tally; state the weighting scheme).
- Ten Gods on the other stems (+ hidden-stem ten gods for the month, the key one).
- 大运 timeline: 起运 age + the decade pillars, with the *current* 大运 marked;
  plus this year's 流年 pillar.

**② 画像 (interpretive — a reflective, LAYERED lens)**

Build the 画像 as **four progressive layers L0→L3 + drill-down** — each deeper,
more granular, more term-heavy than the last. The reader chooses how far to go, so
"easy to understand" (L0/L1) and "comprehensive" (L2/L3) stop fighting. Keep the
①facts / ②lens split (that's the honesty spine); all the richness goes in ②.
Ground every line in `data/content/bazi-life-arc.md` (dimension + life-arc tables)
and speak in `bazi-interpretation.md §E`'s voice.

**Two rules baked into every layer below:**
- **Jargon-gloss rule.** A 十神/五行/扶抑 term appears only in `术语(大白话)` form
  on first use, e.g. `正官(责任、规矩、把自己嵌进体系做好)`、`偏印(靠钻研、悟、偏冷门
  专精的学习方式)`、`比劫(同类、手足、同侪)`. L0/L1 use **zero** terms.
- **Honesty-language rule (from §E).** Use season/agency shapes —「这十年是…的季节」
 「传统上倾向于…」「一种读法是…」「值得留意的是…」「适合…的事」. Never §E-forbidden
  shapes (dated events, 一劫/血光/死, guaranteed 婚/财/离, curses, diagnoses,
  fabricated numbers). Health = tendency + "找医生", never diagnosis.

Top de-noise: fold the disclaimer + archive-settings + birth-echo into ONE small
folded line ABOVE L0 (info kept, not hogging the opening). The real opener is L0.

---

**🪞 L0 · 一句话画像** *(the single most important line — zero jargon, friend-voice)*
One sentence that concentrates the day-master + dominant 十神 into "what kind of
person you are". First-glance payoff for the term-averse reader.
> 例（癸亥·男）：你是那种「心思深、看得细、认死理要把事做扎实」的人——想法在心里转很久，
> 但一旦认准，会用很务实的方式一点点把它做出来。

**✍️ L1 · 性格速写** *(3–4 plain lines, still zero jargon)*
`**加粗引导词** + 一句大白话`, each ≤2 lines. What they're *like day to day*.
> - **想得深、感受细**：遇事先在心里过一遍，共情强，但容易想太多、把别人情绪吸到自己身上。
> - **有主见、独立**：脑子转得快、像水一样能变通，认准方向不太受人左右。
> - **务实、重承诺**：不爱空谈，喜欢把价值一点点建起来，答应的事会当真。
> - **在意规矩和责任**：「该达标、该做好」在你心里分量很重。

**🔎 L2 · 分层面** *(the 7 dimensions — first comprehensiveness block)*
Seven skippable blocks — **事业 / 财 / 感情 / 健康 / 家庭 / 学业 / 性格** — each
`**层面名** → 一句结论 → 半句依据(术语就地夹注)`, ≤3 lines, whole-block skippable,
gender-noted where 财/官/食伤 apply. Source each from `bazi-life-arc.md §1`.
> **💼 事业**：适合「深耕型、能出成果又被认可」的路子。你盘里正官(责任、规矩、嵌进体系
> 做好)藏得厚，食伤(想表达、把心里的东西做出来)也在——既想靠谱，又想有作品感。
> **💰 财运**：偏「稳稳积累」而非「一把暴富」。正财(踏实赚取、务实攒钱那条线)清晰，靠专业
> 和长期复利比投机更合你。(男命:财也关联伴侣缘。)
> **❤️ 感情**：重投入、也重承诺，但心思私密、不轻易外露。是认真型；要留神想太多、把对方
> 情绪都接过来会累。
> **🩺 健康(只谈倾向，不诊断)**：你水最旺(水主思虑、睡眠、情绪)，最该照看「脑子停不下来」
> ——熬夜、反刍、情绪内耗是主要消耗口。给思绪挖出口比硬扛好；真有担心请找医生。
> **👪 家庭/六亲**：比劫(同类、手足、同侪)有力——同辈/朋友/合伙戏份重，是助力也需边界。
> 印偏偏印，靠自己钻研多于被喂养。
> **📚 学业**：自学型。偏印(靠钻研、悟、偏冷门专精)是底色，适合深挖一门慢慢成专家。
> **🧭 性格(底色)**：日主癸水(这张盘的「你」，阴水，雨露雾气的意象)：敏感、想象力强、
> 温和、心思深而私密。有目标时是动力，没出口时容易变成停不下来的盘算。

Then the strength read as **one skippable gray line** (the most term-heavy thing,
demoted out of the body):
> `> 小字·可跳过：按「扶抑」一派估，这盘帮身≈8/耗身≈9、接近中和(near-balanced)——给你`
> `> 出口(表达、扛压、出成果)行，给支持(学习、盟友)也行，不被锁死在单一策略。这只是一种`
> `> 流派的启发，调候/病药等别的读法可能不同，我不替你钉死。`

**🕰️ L3 · 分阶段** *(the life-arc timeline — second comprehensiveness block; the
piece the old output missed entirely)*

> The worked example below is **illustrative formatting only** — its decade pillars are
> not the ones any real chart will produce. Always walk the actual
> `luck_pillars.pillars[]` from the JSON, including the direction (顺/逆行), which
> depends on year polarity × gender.
Walk **every** 大运 decade from `luck_pillars.pillars[]` as a table:
`年龄·干支(白话标签) → 基调一句话 → 最吃重的1–2层面`. Mark the current decade
`👉 当前`. Use the row's `ten_god` for the label and `favor` for valence — **but if
`favor`=平 (near-balanced chart), show the 十神 theme only and do NOT invent a
喜/忌; note that a 偏弱/偏强 chart would carry 喜/忌 per row.** Then give the current
decade +2 lines (基调 + 近期流年 from `upcoming_annual_pillars[]`). Don't expand
every row into prose — that recreates the wall; depth lives in the drill-down.
> | 年龄·大运 | 基调(一句话) | 最吃重的层面 |
> |---|---|---|
> | 3–12 乙卯(食神·表达萌芽) | 爱表达、点子多的童年，启蒙靠好奇心 | 学业·性格 |
> | 13–22 甲寅(伤官·锋芒) | 想法外冒、有点不服管的青春期，才华与叛逆同框 | 学业·性格 |
> | 23–32 癸丑(比肩·自立) | 靠自己站稳的立业期，同辈/伙伴戏份重 | 事业·家庭六亲 |
> | 👉 33–42 壬子(劫财·水旺自我) 当前 | 认准方向、往深里推的季节；自我/独立/协作被放大 | 事业·财运·性格 |
> | 43–52 辛亥(偏印·内省沉淀) | 转向内修与专精，学习滋养多，适合沉下来打磨 | 学业·健康 |
> | 53–62 庚戌(正印+官·收成) | 名望与责任并至的收获期，积累开始变成位置 | 事业·家庭六亲 |
>
> *(本盘近中和，扶抑不给硬喜忌，上面按十神主题读；若为偏弱/偏强，每行会再带 喜/忌。)*
>
> 👉 当前这步(壬子，33–42)：
> - **基调**：一步水运叠在水旺盘上——「往深里推进、认准方向」的好季节；水旺要挖渠，有明确
>   目标和出口时是动力，没出口时容易空转、算个不停。
> - **近期流年**：今年 2026 丙午是火、是正财(务实的成果与收入)被点亮的一年——落到具体、
>   摸得着的成果上会更有共鸣。这是「适合去做」的季节感，不是「你会怎样」的预言。

**结尾 · 下钻入口** *(aligned to L2 dimensions + L3 stages — not three floating options)*
> 想看哪一块更细？点一个我展开：
> 📖 按【层面】深挖 —— 事业/财运/感情/健康/家庭/学业/性格，挑一个
> 🕰️ 按【阶段】展开 —— 任意一步大运(尤其当前33–42)，我细说怎么借势
> 🎯 已经很准的那条 —— 直接说「性格那面镜子最像我」，我顺着聊
> (事业方向我还有专门的职业模块能更系统地做；要的话我接过去。)
> 要不要把这次起盘+画像记进你的私人档案，方便回看？另外，你希望我怎么称呼你？

When the user takes a 【层面】or【阶段】offer, expand with the FULL `bazi-life-arc.md`
§4 recipe (十神 register × favor valence × age-band × 流年 weather) — richly, but
still in §E's voice: **detail from symbolism, never from invented events/dates.**
If they push for a literal year ("到底哪年结婚/发财"), run the §E5 three-beat
(name the want → give the season → kindly decline the fabricated specific).

**One honest caveat** where the chart is uncertain (unknown time → no hour pillar;
23:00 子时 or 立春 boundary; TST shift) — surface from the JSON `ambiguities`.

## 5. Comprehensive AND human-sized — the layers are how
The user wants depth (各阶段各层面) *and* readability. Don't choose — that's exactly
what L0→L3 is for: the opening lands in plain words (L0/L1), the comprehensiveness
lives in skimmable blocks and a timeline (L2/L3), and anything deeper is one
drill-down away. So: never a wall of prose, but never skip a dimension or a decade
either. Make each block short and skippable; let *structure*, not *omission*, keep
it digestible. If a section would balloon, compress it to its one-line essence and
offer to expand — the chart is theirs to revisit, so there's no need to cram
everything into one message.

## 6. Western natal chart (星盘 — real ephemeris, built)
Offer this when the person asks for a **星盘 / natal chart / astrology reading** (not
八字). It is a genuine astronomical chart, not a fake — same honesty spine as BaZi:
the sky is fact, the meaning is a lens.

**First, backfill coordinates if missing.** If `birth.place` is known but
`birth.lat`/`birth.lon`/`birth.tz_at_birth` are null (older profiles, or onboarding
that only got the city name), **derive them from the place and persist via
`set-profile` before computing** — otherwise the Ascendant/houses can never compute.
City coordinates and the UTC offset in effect at that date are public reference facts,
not fabrication; `tz_at_birth` must reflect any historical DST/zone in force then (see
`onboarding.md` Tier 1). Only truly leave them null if the place itself is unknown.

```bash
python3 $D/scripts/astro.py --natal \
  --date <birth.date> [--time <birth.time>] \
  [--lat <birth.lat> --lon <birth.lon> --tz <birth.tz_at_birth>]   # for 上升/宫位
  --format json      # or text for a quick human view
```
`--tz` takes the **IANA zone name** straight from the profile (`Asia/Shanghai`) — it
resolves the historical offset for that birth moment itself and records how in
`caveats`. A bare hour offset still works. Don't compute the DST offset by hand.

**What it computes (facts):** all ten bodies + North Node in sign & degree, natal
retrogrades, the major natal aspects, and — **only when birth time + place + tz are
all known** — the Ascendant, Midheaven, and Placidus house cusps.

**Honest degradation is the whole point — read the JSON `caveats` and state them:**
- **No birth time** → the Moon is flagged `approximate` (it moves ~12–15°/day, so its
  sign can be wrong) and there is **no Ascendant/houses**. Say so; don't guess a rising.
- **No birth place (lat/lon)** or **no tz** → **no Ascendant/houses** (they're
  place- and exact-time-specific). Planets-in-signs still hold for the slow bodies.
- Never fabricate a rising sign, a house placement, or a "your Moon is definitely X"
  when the data can't support it. Omit, and name what's missing (a birth time / place
  / timezone would let you compute it).

**Interpretation** = same rules as everywhere: Sun/Moon/Rising and aspects are a
reflective, cultural lens — "one way to read this", agency language, no fatalism, no
event/date prediction, no fabricated authority. Glossed plain-language on first use,
just like the 十神 gloss rule. If asked to compare with the BaZi, treat them as **two
independent symbolic languages** describing the same person — note where they rhyme,
never claim one "proves" the other.

## Honesty checklist (before sending)
- Facts and lens visibly separated? Disclaimer note present once?
- Any life prediction, illness/marriage/wealth certainty, or fabricated figure? →
  remove or reframe as tendency.
- Ambiguities from the JSON surfaced? Agency language throughout?
- Natal chart: if `birth.place` was known but coords were null, did I backfill
  `lat`/`lon`/`tz_at_birth` (so Ascendant/houses can compute) rather than silently
  shipping a chart with no houses? Did I read `caveats` and state every omission
  (rising/houses/Moon) instead of guessing?
- Did I read `cross_check_sxtwl.agrees` (not eyeball the two ganzhi), and surface any
  立春-proximity ambiguity?
- **Machine backstop:** `python3 $D/scripts/selfcheck.py --module destiny --file draft.md` — exit 1 means a blocker; fix it before sending. Passing is not proof it's honest, only that it's free of the known bad shapes.
