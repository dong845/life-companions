#!/usr/bin/env python3
"""
safety_scan.py — a keyword/regex BACKSTOP for crisis and abuse signals.

This is deliberately a low-tech safety net, NOT the primary detector. The model
reading references/safety.md is the real safeguard: it understands context, irony,
and quoting in ways a keyword list never will. This script exists so that (a) a
crisis phrase in a logged entry sets `crisis_flag` in the index for cheap
longitudinal scanning, and (b) there is a mechanical floor if the model ever
misses something. It errs toward flagging; a flag means "slow down and look",
never "auto-respond with a script".

Patterns cover both English and Chinese because the output language is the user's
choice. Returns categories, not a diagnosis.

Usage:
  echo "text" | python3 safety_scan.py         # reads stdin
  python3 safety_scan.py --text "..."
  # or import: from safety_scan import scan_text
"""
import argparse
import json
import re
import sys

# Each category: list of regexes. Kept specific-ish to limit false positives,
# but recall is prioritized over precision — this is a backstop.
PATTERNS = {
    "self_harm_suicide": [
        r"\bkill(ing)?\s+myself\b", r"\bend(ing)?\s+(it|my life)\b",
        r"\bsuicid", r"\bwant(ed)?\s+to\s+die\b", r"\bno\s+reason\s+to\s+live\b",
        r"\bdon'?t\s+want\s+to\s+(live|be here|wake up|go on)\b",
        r"\bself[-\s]?harm", r"\bcut(ting)?\s+myself\b", r"\boverdos",
        r"\bbetter\s+off\s+(dead|without me)\b", r"\bcan'?t\s+see\s+the\s+point\b",
        # Chinese — cover common phrasings AND variants that a naive list misses
        r"不想活", r"活不下去", r"撑不下去", r"活着没(什么|啥)?(意思|意义)",
        r"活着.{0,3}没(什么|啥)?意思", r"不想(活了|醒来|存在)", r"活着(好累|没劲|真累)",
        r"自杀", r"结束(自己的|这一切|生命)", r"轻生", r"了结(自己|生命)",
        r"一了百了", r"走了算了", r"生无可恋",
        r"伤害自己", r"自残", r"想死", r"死了算了", r"没有活下去的意义",
    ],
    "abuse_violence": [
        r"\bhit(s|ting)?\s+me\b", r"\bhurt(s|ing)?\s+me\b", r"\bafraid\s+of\s+(him|her|them|my)\b",
        r"\bthreaten", r"\bchoke", r"\bforced?\s+me\b", r"\bwon'?t\s+let\s+me\b",
        r"\bcontrols?\s+(my|me|what i)\b", r"\bisolat(e|ed|ing)\s+me\b",
        r"打我", r"动手", r"家暴", r"威胁我", r"不让我", r"控制我", r"害怕(他|她|回家)",
        r"逼我(?!加班|上班|工作|学习|干活|写|做作业|还钱)", r"掐(我|住)", r"跟踪我",
    ],
    # Coercive control — often the person doesn't call it abuse; pattern-of-control
    # signals matter. Recall-prioritized backstop (the model is the real detector).
    "coercive_control": [
        r"\bchecks?\s+my\s+phone\b", r"\bgoes?\s+through\s+my\s+phone\b",
        r"\btracks?\s+my\s+(location|phone)\b", r"\bmonitors?\s+me\b", r"\bfollows?\s+me\b",
        r"\bcontrols?\s+(my|the)\s+(money|finances|spending)\b",
        r"\bwon'?t\s+let\s+me\s+(see|go|talk|leave|work)\b",
        r"\bisolat(e|ed|ing|es)\s+me\s+from\b", r"\bwalking\s+on\s+eggshells\b",
        r"看我(的)?手机", r"查(我|你)?(的)?手机", r"翻我(的)?手机",
        r"查我(在哪|位置|岗)", r"查岗", r"打电话查(我|岗|位置)",
        r"规定我(能)?(花|见|去|联系)", r"管我(的钱|花钱|花多少)", r"不给我(钱|花钱)",
        r"不许我", r"不准我", r"不让我(出|见|联系|去|工作|上班)",
        r"孤立我", r"不敢(联系|见)(朋友|家人|以前)", r"监视我?",
        r"(挺|很|好|有点|真)怕(他|她|回家|你|老公|老婆|对象|男朋友|女朋友|男友|女友|伴侣)",
        r"如履薄冰", r"小心翼翼(地)?(过|生活|相处)",
    ],
    "acute_distress": [
        r"\bcan'?t\s+(do this|go on|take (it|this))\b", r"\bhopeless\b",
        r"\bbreak(ing)?\s+down\b", r"\bpanic attack\b", r"\bcan'?t\s+stop\s+crying\b",
        r"崩溃", r"撑不住", r"绝望", r"喘不过气", r"控制不住", r"停不下来地哭",
    ],
}


def scan_text(text):
    text = text or ""
    low = text.lower()
    hits = {}
    for cat, pats in PATTERNS.items():
        matched = []
        for p in pats:
            m = re.search(p, low)
            if m:
                matched.append(m.group(0))
        if matched:
            hits[cat] = matched
    # severity: self_harm / abuse / coercive-control => high; only acute_distress => watch
    if any(c in hits for c in ("self_harm_suicide", "abuse_violence", "coercive_control")):
        severity = "high"
    elif hits:
        severity = "watch"
    else:
        severity = "none"
    return {
        "crisis_flag": bool(hits),
        "severity": severity,
        "categories": list(hits.keys()),
        "matched": hits,
        "_action": (
            "If flagged: STOP the fortune/advice persona. See references/safety.md. "
            "This is a backstop, not a verdict — read the actual context."
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=None)
    args = ap.parse_args()
    text = args.text if args.text is not None else sys.stdin.read()
    print(json.dumps(scan_text(text), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
