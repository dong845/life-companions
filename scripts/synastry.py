#!/usr/bin/env python3
"""
synastry.py — the traditional relations BETWEEN two BaZi charts (合婚), computed.

Read this before using it. 合婚 is the single place where 命理 does the most
real-world damage: people leave relationships that were fine because someone told
them 属相不合. So this script is built to make the honest half easy and the harmful
half impossible:

  * It computes REAL, checkable, traditional relations — 六合/三合/六冲/相刑/相害/
    相破 between the two charts' branches, the 十神 relation between the two day
    masters, and whether one chart's element balance complements the other's.
  * It emits NO verdict, NO score, NO 合/不合, NO percentage, and no recommendation
    about the relationship. There is deliberately no field you could print as one.

The reason is not squeamishness, it is accuracy: the branch relations are facts
about two sets of symbols. Whether two people do well together is made of what they
actually do — how they repair after a fight, whether they want the same decade —
and no arrangement of 干支 knows any of that. The relationship module
(`references/modules/relationships.md`) is where an actual relationship question
belongs; this is a cultural lens laid beside it, never in place of it.

Usage:
  python3 synastry.py --a 1993-04-12 --a-time 07:35 --a-gender m \\
                      --b 1995-08-30 --b-time 14:20 --b-gender f --format text
  (birth times are optional for both sides; the hour pillar is simply omitted)
"""
import argparse
import json
import os
import sys

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bazi  # noqa: E402

# ---------------------------------------------------------------------------
# Standard 地支 relation tables. These are fixed, disclosed, and checkable — the
# whole point is that a reader can verify them against any 子平 text.
# ---------------------------------------------------------------------------
ZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

LIUHE = {  # 六合 — the classic pairings
    frozenset(("子", "丑")): "土", frozenset(("寅", "亥")): "木",
    frozenset(("卯", "戌")): "火", frozenset(("辰", "酉")): "金",
    frozenset(("巳", "申")): "水", frozenset(("午", "未")): "土",
}
SANHE = {  # 三合局 — full triads
    frozenset(("申", "子", "辰")): "水", frozenset(("亥", "卯", "未")): "木",
    frozenset(("寅", "午", "戌")): "火", frozenset(("巳", "酉", "丑")): "金",
}
SANHUI = {  # 三会方 — seasonal assemblies
    frozenset(("寅", "卯", "辰")): "木", frozenset(("巳", "午", "未")): "火",
    frozenset(("申", "酉", "戌")): "金", frozenset(("亥", "子", "丑")): "水",
}
LIUCHONG = [("子", "午"), ("丑", "未"), ("寅", "申"),
            ("卯", "酉"), ("辰", "戌"), ("巳", "亥")]          # 六冲
LIUHAI = [("子", "未"), ("丑", "午"), ("寅", "巳"),
          ("卯", "辰"), ("申", "亥"), ("酉", "戌")]            # 六害(穿)
LIUPO = [("子", "酉"), ("午", "卯"), ("申", "巳"), ("寅", "亥"),
         ("辰", "丑"), ("戌", "未")]                            # 六破
XING = {  # 三刑 / 自刑
    "无恩之刑": ("寅", "巳", "申"),
    "恃势之刑": ("丑", "戌", "未"),
    "无礼之刑": ("子", "卯"),
}
SELF_XING = ("辰", "午", "酉", "亥")

# Plain-language notes. Each says what the relation MEANS in the tradition, framed as
# a texture to notice, never as an outcome. These are the only interpretive strings
# here, and they are deliberately about interaction style, not fate.
RELATION_NOTES = {
    "六合": "传统上读作「合」——彼此拉近、容易黏在一起的一组关系；亲密顺，边界要自己划。",
    "三合": "同一局的三支，传统上读作方向一致、容易结成同盟的组合。",
    "半合": "三合缺一角，传统上读作有共同方向但没那么严丝合缝。",
    "三会": "同一季的三支，传统上读作气场同类、待在一起很自然。",
    "六冲": "传统上读作「冲」——张力、推拉、彼此摇动。冲不等于坏：很多长久的关系就是靠这股张力保持清醒；它说的是这段关系需要更多明说，不是它会散。",
    "六害": "传统上读作「害/穿」——容易误会、觉得没被看见的一组关系；要靠讲清楚，不是靠忍。",
    "六破": "传统上读作「破」——节奏容易被打断、计划容易变卦的一组。",
    "相刑": "传统上读作「刑」——同一件事上反复磨；这一类最需要明确的规则和边界。",
    "自刑": "同支相见，传统上读作自己跟自己较劲的那一面被放大了。",
}

PILLAR_MEANING = {  # which pillar the tradition reads for what, in a couple's chart
    "year": "年柱（传统上看根基/家庭背景，也是「属相」那一格）",
    "month": "月柱（传统上看成长环境与做事节奏）",
    "day": "日柱（日支即传统的「夫妻宫」——两人相处最直接的一格）",
    "hour": "时柱（传统上看晚年与共同的落点）",
}

TEN_GOD_TEXTURE = {  # A's day master seen FROM B's day master — a relational register
    "比肩": "同类——像同侪、像队友，好懂，但也容易比较。",
    "劫财": "同类而异性——吸引强、卷入也强，边界感是功课。",
    "食神": "输出——放松、被滋养、愿意分享的那一面。",
    "伤官": "输出而锋利——被激发、也容易被戳到；才气与摩擦同源。",
    "正财": "所克而异性——务实的珍惜、想把对方安置好。",
    "偏财": "所克而同性——松弛、大方，但也容易不聚焦。",
    "正官": "被克而异性——尊重、责任、想守规矩把关系做对。",
    "七杀": "被克而同性——压力感强、被推着长大；强度高，缓冲要自己造。",
    "正印": "被生而异性——被照顾、被理解的那一面。",
    "偏印": "被生而同性——被理解但也容易被看穿，亲密里带一点距离。",
}


def _pair(a, b):
    return frozenset((a, b))


def _relations_between(z1, z2):
    """Every traditional relation between two branches. Two branches can carry more
    than one at once (寅亥 is both 六合 and 六破) — return all of them rather than
    picking the flattering one."""
    out = []
    if z1 == z2 and z1 in SELF_XING:
        out.append({"relation": "自刑", "detail": f"{z1}{z2}"})
    if _pair(z1, z2) in LIUHE:
        out.append({"relation": "六合", "detail": f"{z1}{z2}合化{LIUHE[_pair(z1, z2)]}"})
    for triad, elem in SANHE.items():
        if z1 in triad and z2 in triad and z1 != z2:
            missing = sorted(triad - {z1, z2})
            out.append({"relation": "半合", "detail": f"{z1}{z2}（{elem}局，缺{missing[0]}）"})
    for triad, elem in SANHUI.items():
        if z1 in triad and z2 in triad and z1 != z2:
            out.append({"relation": "三会", "detail": f"{z1}{z2}（会{elem}方）"})
    for x, y in LIUCHONG:
        if {z1, z2} == {x, y}:
            out.append({"relation": "六冲", "detail": f"{z1}{z2}冲"})
    for x, y in LIUHAI:
        if {z1, z2} == {x, y}:
            out.append({"relation": "六害", "detail": f"{z1}{z2}害"})
    for x, y in LIUPO:
        if {z1, z2} == {x, y}:
            out.append({"relation": "六破", "detail": f"{z1}{z2}破"})
    for name, group in XING.items():
        if len(group) == 2:
            if {z1, z2} == set(group):
                out.append({"relation": "相刑", "detail": f"{z1}{z2}刑（{name}）"})
        elif z1 in group and z2 in group and z1 != z2:
            out.append({"relation": "相刑", "detail": f"{z1}{z2}（{name}，全见{''.join(group)}才成三刑）"})
    return out


def _ten_god(from_gan, to_gan):
    """The 十神 of `to_gan` seen from `from_gan` — reuses bazi.py's own table so the
    two scripts can never drift apart."""
    return bazi._ten_god(from_gan, to_gan)


def compare(a_args, b_args):
    a = bazi.compute(**a_args)
    b = bazi.compute(**b_args)
    ap, bp = a["computed"]["pillars"], b["computed"]["pillars"]

    pairs = []
    for key in ("year", "month", "day", "hour"):
        if not ap.get(key) or not bp.get(key):
            continue
        z1, z2 = ap[key]["zhi"], bp[key]["zhi"]
        rels = _relations_between(z1, z2)
        pairs.append({
            "pillar": key,
            "pillar_means": PILLAR_MEANING[key],
            "a_zhi": z1, "b_zhi": z2,
            "relations": rels,
            "notes": [RELATION_NOTES[r["relation"]] for r in rels],
        })

    # Day masters seen from each other — the most-used relational read in 子平.
    a_dm, b_dm = ap["day"]["gan"], bp["day"]["gan"]
    dm = {"a_day_master": a_dm, "b_day_master": b_dm}
    tg_ab = _ten_god(a_dm, b_dm)
    tg_ba = _ten_god(b_dm, a_dm)
    if tg_ab:
        dm["b_seen_from_a"] = {"ten_god": tg_ab, "texture": TEN_GOD_TEXTURE.get(tg_ab)}
    if tg_ba:
        dm["a_seen_from_b"] = {"ten_god": tg_ba, "texture": TEN_GOD_TEXTURE.get(tg_ba)}

    # Element complementarity, stated as counts only. NOT a compatibility score:
    # "who has more of what" is arithmetic; "therefore you fit" is not.
    at = a["computed"]["element_tally"]["with_hidden"]
    bt = b["computed"]["element_tally"]["with_hidden"]
    combined = {k: at.get(k, 0) + bt.get(k, 0) for k in set(at) | set(bt)}
    a_scarce = sorted(at, key=lambda k: at[k])[:2]
    b_scarce = sorted(bt, key=lambda k: bt[k])[:2]
    elements = {
        "a_tally": at, "b_tally": bt, "combined_tally": combined,
        "a_thinnest": a_scarce, "b_thinnest": b_scarce,
        "b_supplies_a": [e for e in a_scarce if bt.get(e, 0) > at.get(e, 0)],
        "a_supplies_b": [e for e in b_scarce if at.get(e, 0) > bt.get(e, 0)],
        "_note": ("Counts only. 'One chart supplies what the other is thin on' is a "
                  "traditional way of NOTICING a complementarity, not evidence that two "
                  "people fit — and a shared thin element is not a shared problem."),
    }

    ambiguities = list(a["ambiguities"]) + list(b["ambiguities"])
    if not ap.get("hour") or not bp.get("hour"):
        ambiguities.append("有一方（或双方）出生时刻未知：时柱不参与比对，这一格的关系读不出来。")

    return {
        "computed": {
            "a": {"pillars": {k: (v["ganzhi"] if v else None) for k, v in ap.items()},
                  "day_master": a["computed"]["day_master"]["as_text"]},
            "b": {"pillars": {k: (v["ganzhi"] if v else None) for k, v in bp.items()},
                  "day_master": b["computed"]["day_master"]["as_text"]},
            "pillar_pairs": pairs,
            "day_masters": dm,
            "elements": elements,
            "tables_used": ("六合/三合/三会/六冲/六害/六破/三刑 — standard 子平 branch "
                            "relation tables, listed in full in synastry.py"),
        },
        "ambiguities": ambiguities,
        "refusals": {
            "no_verdict": ("This payload contains no 合/不合, no compatibility score, no "
                           "percentage, and no recommendation — by construction, not by "
                           "omission. Do not synthesize one."),
            "zodiac_myth": ("If they ask 属相合不合: say plainly that 年支 is ONE of four "
                            "pillars and the tradition itself never decided a marriage on "
                            "it alone. Naming a 冲 or 害 as a reason to leave or not start "
                            "a relationship is the harm this module exists to avoid."),
            "route": ("A real relationship question — should we stay, why do we keep having "
                      "this fight — belongs in references/modules/relationships.md, which "
                      "works from what actually happened between them. This is a cultural "
                      "lens laid beside that, never instead of it."),
        },
        "disclaimer": ("以上地支关系、十神关系、五行统计，是按传统规则算出来的可复现事实；"
                       "它们「意味着什么」是一种文化视角下的反思，不是预测，更不是这段关系"
                       "该不该继续的依据。两个人合不合，是你们怎么相处决定的。"),
    }


def _text(r):
    c = r["computed"]
    L = ["两盘对照（合婚 · 传统关系）", ""]
    L.append(f"  A: {' '.join(v for v in c['a']['pillars'].values() if v)}   日主 {c['a']['day_master']}")
    L.append(f"  B: {' '.join(v for v in c['b']['pillars'].values() if v)}   日主 {c['b']['day_master']}")
    L.append("")
    L.append("地支关系（逐柱，事实）：")
    for p in c["pillar_pairs"]:
        head = f"  {p['pillar_means']}  {p['a_zhi']} × {p['b_zhi']}"
        if not p["relations"]:
            L.append(head + " → 无特殊关系")
            continue
        L.append(head + " → " + "、".join(f"{x['relation']}({x['detail']})" for x in p["relations"]))
        for n in p["notes"]:
            L.append(f"      · {n}")
    dm = c["day_masters"]
    L.append("")
    L.append("日主之间（传统上最常看的一格）：")
    for k, label in (("b_seen_from_a", "从 A 看 B"), ("a_seen_from_b", "从 B 看 A")):
        if k in dm:
            L.append(f"  {label}：{dm[k]['ten_god']} —— {dm[k]['texture']}")
    e = c["elements"]
    L.append("")
    L.append("五行（只报数）：")
    L.append(f"  A 最薄：{'/'.join(e['a_thinnest'])}   B 最薄：{'/'.join(e['b_thinnest'])}")
    if e["b_supplies_a"]:
        L.append(f"  B 那边多出来、A 这边偏薄的：{'/'.join(e['b_supplies_a'])}")
    if e["a_supplies_b"]:
        L.append(f"  A 那边多出来、B 这边偏薄的：{'/'.join(e['a_supplies_b'])}")
    if r["ambiguities"]:
        L.append("")
        L.append("需要说明：")
        for a in dict.fromkeys(r["ambiguities"]):
            L.append(f"  · {a}")
    L.append("")
    L.append(r["disclaimer"])
    L.append("")
    L.append("（本输出刻意不含「合/不合」的结论、分数或建议。真正的关系问题请走关系模块。）")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Traditional relations between two BaZi charts")
    ap.add_argument("--a", required=True, help="person A birth date YYYY-MM-DD")
    ap.add_argument("--a-time", default=None)
    ap.add_argument("--a-gender", default="m", choices=["m", "f", "male", "female"])
    ap.add_argument("--b", required=True, help="person B birth date YYYY-MM-DD")
    ap.add_argument("--b-time", default=None)
    ap.add_argument("--b-gender", default="f", choices=["m", "f", "male", "female"])
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()
    try:
        r = compare({"date": args.a, "time": args.a_time, "gender": args.a_gender},
                    {"date": args.b, "time": args.b_time, "gender": args.b_gender})
    except (ValueError, TypeError) as e:
        print(json.dumps({"ok": False, "error": f"bad input: {e}"}, ensure_ascii=False))
        raise SystemExit(2)
    print(_text(r) if args.format == "text"
          else json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
