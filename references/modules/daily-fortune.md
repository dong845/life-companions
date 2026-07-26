# Module: Daily fortune & check-in (每日运势)

The recurring companion hook — and a **rich, multi-system** daily reading, the way
market 运势 products are (西方星座 · 八字流日 · 生肖 · 五行), but with the skill's
line held: **every layer is computed from a real system (八字/生肖 traditional
rules, real astronomy) and labeled; interpretation is reflective, never a
prediction; and nothing is fabricated** — no invented "综合运 ⭐⭐⭐⭐", no made-up
lucky number. What makes it *yours* rather than a generic horoscope is that it's
woven with your journal + continuity. Pairs with `references/journaling.md`.

## Flow
1. **Safety + continuity first, always.** Load `continuity.yaml` + recent entries
   (`companion.py trend --days 14`, `journal --since …`). A heavy entry → care, not
   fortune (safety.md).
2. **Compute the day from real systems** (never hand-compute):
   ```bash
   D=<skill-dir>
   python3 $D/scripts/bazi.py  --date <birth.date> [--time <birth.time>] --gender <m|f> \
     --on-date today --format json     # → computed.daily: 流年/流月/流日 十神+favor,
                                        #   zodiac_day (生肖 vs 日支), wuxing_tips
   python3 $D/scripts/astro.py --date <birth.date> [--time <birth.time>] \
     --on-date today --format json     # → 星座/双鱼…, 今日月亮星座, 逆行, 本命相位
   ```
   Read `data/content/bazi-life-arc.md §1` for the 十神→dimension mappings that turn
   the 流日/流月 十神 into per-life-area reads. No birth data → skip charts, keep it
   journal + a general seasonal note; never fake a chart. `favor` is `平` for a
   near-balanced chart → read the 十神 theme, don't manufacture 宜/忌 or 五行 tips.

## Deliver — the daily card (rich but scannable; ~a screen, not an essay)
Open with a one-line disclaimer note once, then, in the person's `locale`/`tone`:

- **🌤 今日基调** — one line from the **流日** 十神 + `favor`, woven with the day's
  overall lean. (e.g. 流日 劫财[喜] = 借力/补给的一天.)
- **🔮 分层面** — 事业 / 财 / 感情 / 健康, each ONE short line: map the **流日 (+流月)**
  十神 onto that dimension via `bazi-life-arc.md §1`, and show the lean as **喜 ↑ /
  平 → / 忌 ↓** — clearly the *disclosed 扶抑 heuristic*, **NOT a cosmic score or a
  star rating**. (Gender-note 财/官/食伤 where relevant.)
- **🐯 生肖今日** — from `zodiac_day`: 属X · 今日与日支的关系(六合/冲/三合/害/刑)→
  its one-line tone. A real traditional relation, not vibes.
- **♓ 星座今日** — from astro.py: X座 · **今日月亮在…座** · any **逆行**(水逆 etc.) ·
  notable **本命相位**. Frame the positions as astronomical fact, the meaning as a
  reflective lens ("水逆传统上提醒…沟通/复盘慢一点" — never "水逆导致你…").
- **🎨 五行小贴士** — `wuxing_tips`: 幸运色/方位/数, verbatim with its label
  ("按你喜用五行的传统对应,图个彩头,不是保证"). If tips are empty (中和), say so;
  don't invent.
- **✅ 宜 / ⛔ 忌** — 2–3 agency-framed nudges synthesized from the above (a repair,
  a rest, a "别拍板大事" when 冲/刑). Never a lucky-number command, never fatalism.
- **💬 结合你近况** — one earned callback to a recent journal entry/thread. This is
  the differentiator; without it, it's just a cookie.

When the systems **agree**, say so (it lands harder); when they **disagree** (e.g.
八字流日[喜] but 生肖刑害), **say that honestly** — "有借力也有磕碰,混着来" — don't
paper over it into a fake single verdict.

## Guardrails
- Disclaimer once. Systems are real (八字/生肖 rules, real ephemeris); meanings are a
  reflective lens, not prediction (safety.md rule 1). The daily lens is exempt from
  §1 rule 6's "verify" duty — it's reflective, not a checkable external fact.
- **No fabrication:** no invented 综合运 score/stars, no lucky number that isn't the
  labeled 五行-derived 彩头, no "the stars will make you…". The per-dimension lean is
  the 扶抑 heuristic, labeled.
- Never fatalistic; agency language; keep it woven with the journal.
- Coverage now: **八字流日 · 生肖 · 西方星座(真实天文)· 五行**. 紫微斗数 daily is a
  future add (heavier — needs a ZWDS engine); say it's coming if asked, don't fake it.
- Close the loop: offer to log the day (`add-entry`, with your reflection), and offer
  a deeper drill-down on any layer. Keep it a daily touch — rich, but not exhausting;
  if they want the quick version, give just 基调 + 宜/忌.
