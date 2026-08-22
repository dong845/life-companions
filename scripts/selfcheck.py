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

Run it on a DRAFT REPLY, never on this skill's own reference files: those quote the
forbidden shapes in order to forbid them ("no invented 综合运 ⭐⭐⭐⭐", 'never "水逆导致
你…"'), and nothing here can tell a counter-example from an example.
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
# The bad outcomes a reading must never forecast. Kept as one alternation and reused,
# because the failure mode isn't a fixed phrase — it's ANY hedge attached to one of
# these. A competent model almost never writes 命中注定; it writes 「大概率保不住」.
_BAD_EVENT = (r"离婚|分手|离|破产|破财|失业|被裁|失败|没戏|生病|得病|病|出事|受伤|"
              r"车祸|意外|坐牢|去世|死|绝症|癌|流产|保不住|散伙|翻车|栽")

FATALISM = [
    (r"命中注定|注定[要会]|在劫难逃|逃不掉的?命|命该如此", "命定/宿命句式"),
    (r"血光之灾|大凶之(年|运|兆)|必有(灾|祸|难)|克(夫|妻|父|母|子)", "灾祸/克亲断语"),
    (r"(一定|必定|肯定|绝对)(会|要)[^。！\n]{0,12}(" + _BAD_EVENT + ")", "确定性坏结局"),
    (r"你(会|将)(在|于)?\s*\d{4}\s*年[^。\n]{0,12}(结婚|离婚|发财|破产|生病|升职)", "指定年份的事件预言"),
    # --- hedged prediction: the REAL failure shape. A probability attached to a life
    # event is still a prophecy; hedging it doesn't make it a reflection. ---
    (r"(大概率|十有八九|八九不离十|多半(会|要)|难免(会|要)|恐怕(会|要)|怕是(要|会)|"
     r"基本(上)?(会|要|没)|铁定|跑不了)[^。！？\n]{0,14}(" + _BAD_EVENT + ")",
     "带概率的坏事预言（对冲过的宿命，仍是预言）"),
    (r"(" + _BAD_EVENT + r")[^。！？\n]{0,8}(是大概率|概率很大|几乎是必然|在所难免)",
     "带概率的坏事预言"),
    # --- veiled prediction: "容易X" is fine for a TENDENCY, not for an EVENT ---
    (r"(容易|难免|小心|当心|注意)[^。！？\n]{0,8}(出事|生病|得病|破财|破产|失业|被裁|"
     r"离婚|分手|车祸|意外|受伤|坐牢|官司)", "把事件说成「容易发生」——事件预言，不是倾向"),
    (r"(本命年|犯太岁|冲太岁|流年不利|大运不好)[^。！？\n]{0,16}(" + _BAD_EVENT + ")",
     "把年份/运势当成坏事的原因"),
    # --- astrology stated as a CAUSE rather than a traditional framing ---
    (r"(水逆|逆行|冲|刑|太岁|凶星|煞)[^。！？\n]{0,10}(导致|造成|使你|害得|让你[^。\n]{0,6}"
     r"(出事|失败|吵|分手))", "把星象说成因果，而不是传统上的提醒"),
    (r"\b(you|you'?ll)\s+(will\s+)?(definitely|certainly|surely)\s+\w+", "English certainty claim"),
    (r"\b(destined|fated)\s+to\b", "English fatalism"),
    (r"\b(you'?re )?(very )?likely to (get (sick|divorced|fired)|lose|fail)\b",
     "English hedged prediction"),
]

# A chart says nothing checkable about a THIRD party's body or fate. 宫位/六亲 describe
# how the person relates to those roles — not the relatives' actual health. Word order
# varies too much for one regex ("你母亲身体会偏弱" / "你母亲会容易生病"), so scan per
# sentence for the combination: a relative + a forecast word + a health/fate noun.
KIN = r"(父亲|母亲|爸爸|妈妈|爱人|配偶|老公|老婆|伴侣|对象|孩子|子女|儿子|女儿|兄弟|姐妹|父母)"
KIN_FORECAST = r"(会|将|容易|大概率|多半|难免|恐怕|偏|比较|不太|注定)"
KIN_SUBJECT = r"(身体|健康|寿|命|婚姻|事业|财|运|" + _BAD_EVENT + r")"
SENT_SPLIT = re.compile(r"[。！？!?\n；;]")


def _kin_claims(text):
    out = []
    for sent in SENT_SPLIT.split(text):
        if not re.search(KIN, sent):
            continue
        if re.search(KIN_FORECAST, sent) and re.search(KIN_SUBJECT, sent):
            out.append(sent.strip()[:120])
    return out


# Fatalistic 宜忌: a daily reading gives agency-framed nudges, never a prohibition.
TABOO = [
    (r"诸事不宜|百无禁忌|忌出(门|行)|不宜出门|闭门不出|忌(动土|嫁娶|安葬|开市)",
     "黄历式禁令（本 skill 的宜忌是有能动性的建议，不是禁令）"),
    (r"(千万|绝对|务必)(别|不要|不能)[^。！？\n]{0,12}(换工作|辞职|结婚|投资|签|买房|出门)",
     "替对方下禁令"),
]

# A reading must never forecast a hiring outcome (career.md: "not a hiring predictor").
HIRING = [
    (r"(基本|多半|大概率|肯定|铁定)?(没戏|没希望|进不去|拿不到|录不上|够不着|白搭)",
     "预测录用结果"),
    (r"(肯定|一定|必然|铁定)[^。！？\n]{0,8}(能进|录取|拿到\s*offer|被录用)", "预测录用结果"),
    (r"\b(you )?(won'?t|will never|definitely won'?t) (get|land) (the )?(job|offer|role)\b",
     "predicting a hiring outcome"),
]

# Precision faked in WORDS rather than digits — dodges every \d+% pattern.
WORD_PRECISION = [
    (r"(契合度|匹配度|吻合度|准确度|把握)[^。！？\n]{0,6}"
     r"(一成|两成|三成|四成|五成|六成|七成|八成|九成|十成|大半|八九成)",
     "用汉字说的假精度"),
    (r"(八成|九成|七成|一半)[^。！？\n]{0,4}(契合|匹配|吻合|准|靠谱)", "用汉字说的假精度"),
]

# The relationship module holds ≥2 perspectives and never labels the absent partner.
RELATIONSHIP_VERDICT = [
    (r"(他|她|对方|你男朋友|你女朋友|你老公|你老婆)(就)?是(个)?(典型的)?"
     r"(PUA|煤气灯|自恋(型|狂)|反社会|渣男|渣女|控制狂|巨婴|妈宝)",
     "给不在场的一方贴临床/人格标签"),
    (r"(你(就)?该|建议你(马上|赶紧)?|趁早|果断)(离开|分手|离婚|甩)",
     "替对方下决定（且在虐待情境里「直接走」可能升高危险）"),
    (r"\b(he|she|they)('s| is| are) (a )?(narcissist|sociopath|gaslighter|abuser)\b",
     "clinical label on the absent partner"),
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
_OPEN, _CLOSE = "（(【[", "）)】]"


def _is_glossed(text, i, term):
    """Is this 十神 explained in plain language at its first use?

    Two legitimate forms, both used by the skill's own examples:
      正官(责任、规矩、把自己嵌进体系做好)   — term OUTSIDE, gloss follows
      乙卯(食神·表达萌芽)                  — term INSIDE the bracket with the gloss
    Only checking the first shape made every L3 timeline row warn, and a warning that
    always fires is one nobody reads.
    """
    end = i + len(term)
    if any(c in text[end:end + GLOSS_WINDOW] for c in GLOSS_CHARS):
        return True
    # look backwards for an enclosing bracket, then forwards for its close
    start = -1
    for k in range(i - 1, max(-1, i - GLOSS_WINDOW), -1):
        if text[k] in _CLOSE:
            break
        if text[k] in _OPEN:
            start = k
            break
    if start < 0:
        return False
    close = -1
    for k in range(end, min(len(text), end + GLOSS_WINDOW)):
        if text[k] in _CLOSE:
            close = k
            break
    if close < 0:
        return False
    inside = text[start + 1:close]
    return len(inside) - len(term) >= 3      # the bracket carries a real explanation

# Strength / 用神 terms are a different rule: they are the most school-dependent,
# openly heuristic part of the whole chart (safety.md §1, bazi.py `heuristic` block).
# They may be used freely — but not bare. Somewhere near them the reply has to say
# this is ONE school's rule of thumb.
STRENGTH_TERMS = ["身强", "身弱", "扶抑", "用神", "喜用神", "忌神", "调候", "偏强", "偏弱"]
HEDGE_RE = re.compile(r"一派|一种|流派|启发|估|大致|不替你(钉|定)|可能不同|别的读法|"
                      r"不同流派|各家|存疑|"
                      r"heuristic|one school|schools?\s|rule of thumb|one reading|"
                      r"one way to read|tradition|may read|read it differently|"
                      r"not settled|isn'?t settled|approximation|"
                      r"不给硬?喜忌|不替你|近中和|接近中和|near.?balanc", re.I)
# Wide enough that the hedge can live in the next sentence — which is where it usually
# is in English ("…sits close to balanced. That's one school's rule of thumb.").
HEDGE_WINDOW = 130

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
    f += _find(text, WORD_PRECISION, "fabrication", "blocker",
               "safety.md §1 rule 4: 「八成契合」 is a fabricated number wearing a word. "
               "Give the band (低/中/高) and its confidence note, nothing sharper.")
    f += _find(text, TABOO, "fatalism", "blocker",
               "daily-fortune.md: 宜/忌 are 2–3 agency-framed nudges ("
               "「今天适合…」「大事先别拍板」), never a 黄历 prohibition or an order.")
    f += _find(text, HIRING, "hiring-prediction", "blocker",
               "career.md: this is a fit-and-direction lens, explicitly NOT a hiring "
               "predictor. Say what the role feeds and what the gap is; never whether "
               "they'd get it.")
    for sent in _kin_claims(text):
        f.append({
            "code": "kin-prediction", "severity": "blocker",
            "why": "a forecast about a relative's health or fate",
            "evidence": sent,
            "fix": "bazi-life-arc.md §2: 宫位/六亲 describe how THIS person relates to "
                   "those roles (closeness, reliance, friction) — the chart says nothing "
                   "checkable about someone else's body or life. Rewrite as their own "
                   "relational tendency, and never forecast a third party.",
        })
    f += _find(text, RELATIONSHIP_VERDICT, "one-sided-verdict", "blocker",
               "relationships.md §G + safety.md §3: hold ≥2 perspectives, voice the "
               "absent partner fairly, tendencies not labels. If it IS abuse, route to "
               "specialist DV help and respect their timing — never 'just leave', which "
               "can escalate danger.")

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
        if _is_glossed(text, i, term):
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
