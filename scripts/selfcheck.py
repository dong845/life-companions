#!/usr/bin/env python3
"""
selfcheck.py — a deterministic gate over a DRAFTED reply, before it is sent.

Why this exists: every honesty rule in this skill (no fabricated precision, no
fatalism, no invented helpline, disclaimer present, jargon glossed, no un-sourced
high-stakes number) lived only in prose checklists spread across five files. Prose
guardrails degrade with model strength — which is exactly the problem when this skill
runs on a weaker or non-Claude agent. A script does not degrade.

  python3 selfcheck.py --module destiny --file draft.md
  python3 selfcheck.py --module daily --text "..."            # or pipe on stdin
  python3 selfcheck.py --module career --file draft.md --json

Exit 0 = no blockers (warnings may still be listed and are worth reading).
Exit 1 = at least one BLOCKER — do not send the draft as-is.

WHAT THIS IS NOT: proof the reply is honest. It matches surface patterns. It cannot
see a fabricated *claim* phrased calmly, a chart read against the wrong pillars, or a
callback to something the person never said. Passing this gate is necessary, not
sufficient — the module checklists still apply.
"""
import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# The canonical crisis resources. Any other phone-number-shaped string in a reply is
# assumed invented until proven otherwise — a hallucinated helpline is the single
# worst failure this skill can produce, and it is invisible to every other check.
# Keep in sync with references/safety.md §2 and the SKILL.md crisis block.
# ---------------------------------------------------------------------------
KNOWN_HELPLINES = {
    "0800-0113", "08000113", "113",          # NL 113 Zelfmoordpreventie
    "988",                                    # US/Canada
    "12356",                                  # China national psych-support line
    "010-82951332", "01082951332",            # Beijing crisis centre
    "400-161-9995", "4001619995",             # 希望24
    "116123", "116 123",                      # UK/ROI Samaritans
    "0800-2000", "08002000",                  # NL Veilig Thuis
    "1-800-799-7233", "18007997233",          # US DV hotline
    "112", "911", "999", "120", "110",        # emergency services
}
PHONE_RE = re.compile(r"(?<![\d.\-])(?:\+?\d[\d\- ]{4,16}\d)(?![\d.\-])")
# Dates look exactly like phone numbers to that regex — and a fact-check block is FULL
# of them ("时效: as of 2026-08"). Excluding them matters more than it sounds: a gate
# that cries wolf on its own required artifact is a gate nobody runs twice.
DATEY_RE = re.compile(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?$|^\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?$")


def _looks_like_a_phone(raw):
    norm = raw.replace(" ", "")
    if DATEY_RE.match(norm):
        return False
    digits = re.sub(r"\D", "", norm)
    return len(digits) >= 6 or norm in KNOWN_HELPLINES

# ---------------------------------------------------------------------------
# Patterns. Each: (code, regex, severity, why, fix)
#   blocker — an outright violation of safety.md; do not send
#   warn    — legitimate sometimes; look at it before sending
# ---------------------------------------------------------------------------
FATALISM = [
    (r"命中注定|注定[要会]|在劫难逃|逃不掉的?命|命该如此", "命定/宿命句式"),
    (r"血光之灾|大凶之(年|运|兆)|必有(灾|祸|难)|克(夫|妻|父|母|子)", "灾祸/克亲断语"),
    (r"(一定|必定|肯定|绝对)(会|要)[^。！\n]{0,12}(离|分手|破产|失败|生病|出事|死)", "确定性坏结局"),
    (r"你(会|将)(在|于)?\s*\d{4}\s*年[^。\n]{0,12}(结婚|离婚|发财|破产|生病|升职)", "指定年份的事件预言"),
    (r"\b(you|you'?ll)\s+(will\s+)?(definitely|certainly|surely)\s+\w+", "English certainty claim"),
    (r"\b(destined|fated)\s+to\b", "English fatalism"),
]

DIAGNOSIS = [
    (r"你(有|患有|得了)[^。\n]{0,6}(抑郁症|焦虑症|双相|躁郁|人格障碍|PTSD|ADHD)", "疾病诊断"),
    (r"\byou\s+(have|are suffering from)\s+(depression|bipolar|anxiety disorder|PTSD|ADHD)\b", "diagnosis"),
    (r"(她|他|对方)是(焦虑型|回避型|自恋型|边缘型)(人格)?(?!倾向)", "把倾向说成固定人格标签"),
]

FAKE_PRECISION = [
    (r"综合运[^。\n]{0,8}[★☆⭐]", "综合运星级评分"),
    (r"[★☆⭐]{2,}", "星级评分"),
    (r"(运势|今日运|财运|桃花运|事业运)[^。\n]{0,6}\d+\s*(分|/\s*10|%)", "运势打分"),
    (r"幸运数字\s*[:：]?\s*\d", "幸运数字"),
    (r"(匹配度|契合度|吻合度|准确率|成功率|命中率|match)\D{0,6}\d+(\.\d+)?\s*%", "百分比匹配度"),
    (r"(月薪|年薪|salary)\D{0,8}\d[\d,，.]*\s*(万|k|K|元|欧|美元|EUR|USD)", "薪资数字"),
    (r"(前|top)\s*\d+(\.\d+)?\s*%|百分位|percentile", "百分位"),
    (r"研究(表明|显示|证明)|studies show|科学(证明|已证实)", "伪造权威"),
]

# Ten Gods: first use must carry a plain-language gloss in brackets
# (destiny.md jargon-gloss rule — 正官(责任、规矩、把自己嵌进体系做好)).
TEN_GODS = ["比肩", "劫财", "食神", "伤官", "正财", "偏财", "正官", "七杀", "偏官",
            "正印", "偏印", "枭神"]
GLOSS_WINDOW = 25  # a gloss may trail the term by a few characters, not just hug it
GLOSS_CHARS = "（(【[「"

# Strength / 用神 terms are a different rule: they are the most school-dependent,
# openly heuristic part of the whole chart (safety.md §1, bazi.py `heuristic` block).
# They may be used freely — but not bare. Somewhere near them the reply has to say
# this is ONE school's rule of thumb.
STRENGTH_TERMS = ["身强", "身弱", "扶抑", "用神", "喜用神", "忌神", "调候", "偏强", "偏弱"]
HEDGE_RE = re.compile(r"一派|一种|流派|启发|估|大致|不替你(钉|定)|heuristic|one school|"
                      r"rule of thumb|可能不同|别的读法")
HEDGE_WINDOW = 60

# High-stakes external-fact markers → safety.md §1 rule 6 + the factcheck.md block.
HIGH_STAKES = [
    r"签证|居留|永居|绿卡|入籍|国籍|移民|工签|工作许可|30\s*%\s*ruling|知识移民",
    r"报税|税率|退税|免税|社保|补贴|助学金|奖学金|贷款利率",
    r"执业(资格|证)|职业资格认证|学历认证|licens(e|ing)|credential",
    r"\bvisa\b|\bresidence permit\b|\bwork permit\b|\bpermanent residen",
]
FACTCHECK_MARKERS = [r"事实核查", r"来源\s*[:：]", r"时效\s*[:：]",
                     r"as of\s+\d{4}", r"你需要?自己确认"]

# Disclaimer / honesty-note markers, per module. Any ONE match satisfies it —
# the wording lives in assets/disclaimers.md and varies by locale and tone.
DISCLAIMER_MARKERS = {
    "destiny": [r"不是科学预测", r"不是预测", r"反思(的)?(镜子|视角|工具)", r"一种(读法|解读|视角)",
                r"文化(性的)?(视角|透镜)", r"not a scientific prediction", r"cultural lens",
                r"reflection", r"reflective"],
    "daily": [r"不是科学预测", r"不是预测", r"反思(的)?(镜子|视角)", r"一种(读法|解读)",
              r"图个彩头", r"not a (scientific )?prediction", r"reflective"],
    "career": [r"中等程度", r"不能预测.*(录取|拿到|hire)", r"不是(正式|验证过的)?测(评|试)",
               r"不是给你贴标签", r"起点", r"moderately", r"not a validated", r"not a label",
               r"don'?t predict"],
    "relationships": [r"只听到了?你(这)?一面", r"看不到全部", r"不是替你下判断", r"几种可能的角度",
                      r"only hearing your side", r"not a verdict"],
}
MODULES = ["destiny", "daily", "career", "relationships", "journal", "crisis", "none"]


def _find(text, patterns, code, severity, fix):
    out = []
    for pat, why in patterns:
        for m in re.finditer(pat, text, re.I):
            out.append({"code": code, "severity": severity, "why": why,
                        "evidence": m.group(0).strip()[:120], "fix": fix})
    return out


def check(text, module="none", locale=None):
    f = []

    f += _find(text, FATALISM, "fatalism", "blocker",
               "safety.md §1 rule 2/3: rewrite as a tendency + agency "
               "(「传统上倾向于…」「一种读法是…」), never an event or a verdict.")
    f += _find(text, DIAGNOSIS, "diagnosis", "blocker",
               "safety.md §1 rule 5: no medical/clinical labels. Say the tendency, "
               "then point to a professional.")
    f += _find(text, FAKE_PRECISION, "fabrication", "blocker",
               "safety.md §1 rule 4: no invented score, percentage, salary, percentile "
               "or authority. Keep it qualitative, or cite a real source.")

    # Any percentage at all is suspect in a reflective reading — the specific shapes
    # above are the common ones, but a number can be invented in a shape nobody listed.
    # Warn (not block): a percentage QUOTED from a dated official source is legitimate,
    # and the 30% ruling is a proper noun.
    for m in re.finditer(r"\d+(?:\.\d+)?\s*%", text):
        ctx = text[max(0, m.start() - 30): m.end() + 30]
        if re.search(r"ruling|来源|as of|时效", ctx, re.I):
            continue
        f.append({"code": "percentage", "severity": "warn",
                  "why": "a percentage — is it quoted from a real, dated source?",
                  "evidence": ctx.replace("\n", " ").strip()[:120],
                  "fix": "safety.md §1 rule 4/6: bands and qualitative language only. "
                         "A real external figure needs 来源 + 时效 (factcheck.md); "
                         "anything else must go."})

    # --- invented helplines -------------------------------------------------
    for m in PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        norm = raw.replace(" ", "")
        if norm in KNOWN_HELPLINES or raw in KNOWN_HELPLINES:
            continue
        if not _looks_like_a_phone(raw):
            continue
        if norm.replace("-", "") in {h.replace("-", "") for h in KNOWN_HELPLINES}:
            continue
        f.append({
            "code": "unknown-helpline", "severity": "blocker",
            "why": "a phone-number-shaped string that is not a known crisis resource",
            "evidence": raw,
            "fix": "safety.md §2: use only the listed numbers, or findahelpline.com. "
                   "NEVER improvise a helpline number — verify it or drop it.",
        })

    # --- un-glossed 十神 -----------------------------------------------------
    for term in TEN_GODS:
        i = text.find(term)
        if i < 0:
            continue
        end = i + len(term)
        if any(c in text[end:end + GLOSS_WINDOW] for c in GLOSS_CHARS):
            continue
        f.append({
            "code": "unglossed-jargon", "severity": "warn",
            "why": f"「{term}」 appears without a plain-language gloss on first use",
            "evidence": text[max(0, i - 18): end + 18].replace("\n", " "),
            "fix": "destiny.md jargon-gloss rule: first use must be 术语(大白话), "
                   "e.g. 正官(责任、规矩、把自己嵌进体系做好). L0/L1 use zero terms.",
        })

    # --- strength / 用神 stated as fact rather than as one school's heuristic --
    for term in STRENGTH_TERMS:
        i = text.find(term)
        if i < 0:
            continue
        window = text[max(0, i - HEDGE_WINDOW): i + len(term) + HEDGE_WINDOW]
        if HEDGE_RE.search(window):
            continue
        f.append({
            "code": "unhedged-strength", "severity": "warn",
            "why": f"「{term}」 is used without saying it is one school's heuristic",
            "evidence": text[max(0, i - 20): i + len(term) + 20].replace("\n", " "),
            "fix": "safety.md §1: 身强弱/用神喜忌 are the `heuristic` half of bazi.py's "
                   "payload, not a computed fact. Label it (「按扶抑一派估…」) and note "
                   "that 调候/病药 schools may read it differently.",
        })

    # --- high-stakes external facts need the factcheck block ----------------
    hs = [m.group(0) for p in HIGH_STAKES for m in re.finditer(p, text, re.I)]
    if hs:
        has_block = any(re.search(p, text, re.I) for p in FACTCHECK_MARKERS)
        if not has_block:
            f.append({
                "code": "missing-factcheck", "severity": "blocker",
                "why": f"touches a high-stakes external fact ({', '.join(sorted(set(hs))[:3])}) "
                       f"with no 来源·时效 fact-check block",
                "evidence": sorted(set(hs))[0],
                "fix": "references/factcheck.md: research a live official source, date it, "
                       "test the conditions against THIS person, and attach the "
                       "「来源 · 时效 · 你需自己确认」 block. No web access → say you "
                       "couldn't verify and route to the source; don't assert it.",
            })

    # --- disclaimer present for the module ----------------------------------
    if module in DISCLAIMER_MARKERS:
        if not any(re.search(p, text, re.I) for p in DISCLAIMER_MARKERS[module]):
            f.append({
                "code": "missing-disclaimer", "severity": "warn",
                "why": f"no honesty/entry note found for module `{module}`",
                "evidence": "",
                "fix": "assets/disclaimers.md: one light standing note per module entry "
                       "(once, not on every line). If you already gave it earlier in this "
                       "conversation, this warning is expected — ignore it.",
            })

    # --- crisis replies must not carry the fortune persona ------------------
    if module == "crisis":
        for pat, why in [(r"运势|命盘|八字|星座|流年|大运|塔罗", "mysticism in a crisis reply"),
                         (r"\b(horoscope|zodiac|fortune|chart)\b", "mysticism in a crisis reply")]:
            for m in re.finditer(pat, text, re.I):
                f.append({"code": "crisis-persona", "severity": "blocker", "why": why,
                          "evidence": m.group(0),
                          "fix": "safety.md §2: drop the fortune/advice persona entirely. "
                                 "Plain human warmth + a real, localized resource."})
        if not any(h in text.replace(" ", "") for h in
                   [n.replace("-", "").replace(" ", "") for n in KNOWN_HELPLINES]) \
           and "findahelpline" not in text.lower():
            f.append({"code": "crisis-no-resource", "severity": "blocker",
                      "why": "crisis reply surfaces no real help resource",
                      "evidence": "",
                      "fix": "safety.md §2 step 3: give a real localized line, or "
                             "findahelpline.com when you don't know their country. "
                             "Never guess a country's number."})

    blockers = [x for x in f if x["severity"] == "blocker"]
    return {
        "ok": not blockers,
        "module": module,
        "blockers": len(blockers),
        "warnings": len(f) - len(blockers),
        "findings": f,
        "_note": "A surface-pattern backstop, NOT proof the reply is honest. It cannot "
                 "see a calmly-worded fabrication, a chart read off the wrong pillars, or "
                 "a callback to something they never said. The module checklists still apply.",
    }


def main():
    ap = argparse.ArgumentParser(description="Honesty gate over a drafted reply.")
    ap.add_argument("--module", default="none", choices=MODULES,
                    help="which lens the draft is for (picks the disclaimer + crisis rules)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text", default=None)
    src.add_argument("--file", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()

    r = check(text, args.module)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if r["ok"] and not r["findings"]:
            print(f"selfcheck [{r['module']}]: OK — no pattern violations.\n{r['_note']}")
        else:
            print(f"selfcheck [{r['module']}]: {r['blockers']} blocker(s), "
                  f"{r['warnings']} warning(s)\n")
            for x in r["findings"]:
                mark = "✖ BLOCKER" if x["severity"] == "blocker" else "· warn"
                print(f"{mark} [{x['code']}] {x['why']}")
                if x["evidence"]:
                    print(f"    evidence: {x['evidence']}")
                print(f"    fix: {x['fix']}\n")
            print(r["_note"])
    sys.exit(1 if r["blockers"] else 0)


if __name__ == "__main__":
    main()
