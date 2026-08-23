#!/usr/bin/env python3
"""
career_match.py — honest interest/values/traits -> occupation congruence.

Computes how well a person's self-reported profile (a transparent RIASEC INTEREST
CHECK, plus optional Work Values and a light Big Five read) matches O*NET
occupations shipped in data/career/occupations.json. It emits COARSE BANDS
(Low / Moderate / Strong) with a CONFIDENCE NOTE — never a fabricated percentage,
salary, demand, or percentile. Raw floats are used only for internal ranking and
are kept OUT of the person-facing payload.

Design honesty rules baked in:
  * The person-side items are an interest CHECK grounded in Holland/RIASEC, NOT
    the O*NET Interest Profiler or any validated instrument (see assessment_items.json).
  * Occupations whose interest signal comes only from a 3-letter high-point code
    (riasec == null) are expanded with a coarse 3-2-1 rule and flagged
    lower-confidence.
  * All magic numbers (weights, band thresholds, the 3-2-1 expansion) are the
    disclosed constants below — auditable and tunable.

Pure standard library (no numpy) so it runs offline anywhere, matching the rest
of this skill's scripts; the linear algebra is trivial on 6-vectors.

Usage:
  python3 career_match.py --selftest
  python3 career_match.py --demo
  # or import: from career_match import score_person, rank_occupations, band, confidence
"""
import argparse
import json
import math
import os
import re

# ------------------------------------------------------------------ constants
# Order is fixed everywhere: Realistic, Investigative, Artistic, Social,
# Enterprising, Conventional.
RIASEC_ORDER = ("R", "I", "A", "S", "E", "C")

# High-point code expansion: 1st letter -> 3, 2nd -> 2, 3rd -> 1, others -> 0,
# then L1-normalized (divide by 6). A coarse reconstruction => lower confidence.
HIGHPOINT_RANK_WEIGHTS = (3, 2, 1)
HIGHPOINT_L1 = float(sum(HIGHPOINT_RANK_WEIGHTS))  # 6.0

# Blend weights over whichever components are present (renormalized). Disclosed.
DEFAULT_WEIGHTS = {"interests": 0.45, "values": 0.30, "traits": 0.25}

# Coarse band thresholds on a 0..1 fit. Design defaults, disclosed as tunable.
# Exhaustively verified over all 720 orderings of the six O*NET work values: the
# ipsative cosine's minimum (an exactly reversed ranking) is this value, not 0.
VALUES_COS_FLOOR = 0.615

BAND_LOW_MAX = 0.55       # score < 0.55 -> "Low"
BAND_MODERATE_MAX = 0.75  # 0.55 <= score < 0.75 -> "Moderate"; >= 0.75 -> "Strong"

INTEREST_ITEM_MAX = 4     # per-item liking response is stored 0..4
FULL_INTEREST_ITEMS = 21  # the full interest-check item bank size

# Canonical work-value order (for ipsative -> preference vector).
WORK_VALUES = ("Achievement", "Independence", "Recognition",
               "Relationships", "Support", "Working Conditions")

DATA_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "career", "occupations.json",
)

# ------------------------------------------------------------------ vec helpers
def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))

def _norm(a):
    return math.sqrt(sum(x * x for x in a))


# ------------------------------------------------------------------ interests
def expand_highpoint_code(code):
    """3-letter (or 1-2 letter) Holland high-point code -> normalized 6-vector.

    'IRC' -> I=3, R=2, C=1, others 0, then /6. Returns [R,I,A,S,E,C].
    """
    code = (code or "").strip().upper()
    vec = [0.0] * 6
    idx = {letter: i for i, letter in enumerate(RIASEC_ORDER)}
    for rank, letter in enumerate(code[:3]):
        if letter in idx:
            vec[idx[letter]] = HIGHPOINT_RANK_WEIGHTS[rank]
    return [v / HIGHPOINT_L1 for v in vec]


def interest_vector_from_ratings(ratings):
    """Six O*NET interest ratings on the 1-7 scale -> 0..1 via (x-1)/6.

    `ratings` is [R,I,A,S,E,C]. Returns a 6-vector.
    """
    return [(float(x) - 1.0) / 6.0 for x in ratings]


def occupation_interest_vector(occ):
    """Return (vector, from_code) for one occupation record.

    Uses six numeric interest ratings when present (occ['riasec'] is a list of
    6 numbers); otherwise expands the verified high_point_code and flags
    from_code=True (lower confidence).
    """
    riasec = occ.get("riasec")
    if isinstance(riasec, (list, tuple)) and len(riasec) == 6 \
            and all(isinstance(x, (int, float)) for x in riasec):
        return interest_vector_from_ratings(riasec), False
    return expand_highpoint_code(occ.get("high_point_code", "")), True


def response_discrimination(responses, scoring_key):
    """How much SHAPE the answers carry, as the spread of the six type means in [0,1].

    Cosine ignores magnitude, so answering the same value to every item yields the
    vector [k,k,k,k,k,k] — identical in DIRECTION for k=1,2,3,4 and carrying no
    information about the person. It still produced a full 188-occupation ranking with
    bands, always topped by whichever occupation sits closest to the uniform direction.
    A flat answer set is a non-answer and has to be refused, not scored.
    """
    vec, _n = person_interest_vector(responses, scoring_key)
    if not vec:
        return 0.0
    return max(vec) - min(vec)


# Below this spread the answers do not distinguish the six types at all.
MIN_DISCRIMINATION = 0.08


def person_interest_vector(responses, scoring_key):
    """Interest-check responses -> normalized person vector [R,I,A,S,E,C].

    responses: dict mapping item_id (int or str) -> liking value 0..4.
    scoring_key: dict type_letter -> list of item_ids belonging to that type.
    Per-type raw = sum of that type's answered items; normalized by
    (n_answered_for_type * INTEREST_ITEM_MAX) so uneven item counts and skipped
    items don't bias the vector. Returns (vector, n_items_answered).
    """
    def get(item_id):
        if item_id in responses:
            return responses[item_id]
        return responses.get(str(item_id))

    vec = []
    total_answered = 0
    for letter in RIASEC_ORDER:
        ids = scoring_key.get(letter, [])
        answered = [(i, get(i)) for i in ids if get(i) is not None]
        if answered:
            raw = sum(float(v) for _, v in answered)
            vec.append(raw / (len(answered) * INTEREST_ITEM_MAX))
            total_answered += len(answered)
        else:
            vec.append(0.0)
    return vec, total_answered


def cosine_congruence(p, o):
    """Normalized cosine similarity of two non-negative 6-vectors -> [0,1].

    Scale-tolerant (someone who 'likes everything' still gets a meaningful shape
    match). Returns None for a degenerate all-zero vector (not scorable).
    """
    denom = _norm(p) * _norm(o)
    if denom == 0:
        return None
    return _dot(p, o) / denom


def euclid_fit(p, o):
    """Documented alternative (selectable): 1 - ||p-o||2 / sqrt(6), in [0,1].

    Each vector lives in the unit 6-cube, so max L2 distance is sqrt(6).
    Default engine stays cosine.
    """
    d = math.sqrt(sum((x - y) ** 2 for x, y in zip(p, o)))
    return 1.0 - d / math.sqrt(6.0)


# ------------------------------------------------------------------ values / traits
# Case/spacing/underscore-tolerant lookup for the six canonical value names, so a
# ranking that says "Working_Conditions" or "working conditions" still resolves.
_VALUE_CANON = {w.lower(): w for w in WORK_VALUES}


def _norm_value_name(k):
    key = re.sub(r"\s+", " ", str(k).replace("_", " ").strip().lower())
    return _VALUE_CANON.get(key)  # canonical name, or None if unrecognized


def canonical_values_ranking(ranking):
    """Normalize a values ranking to {canonical_name: int rank} covering ALL six,
    or return None if it can't (missing a value, unknown name, non-int rank).

    Returning None (rather than raising) is deliberate: an incomplete ranking — a
    form where the person left a value unranked — must DEGRADE the read to
    interests-only, never crash the whole scoring run. Accepts a dict or an ordered
    list (index 0 = most important)."""
    if isinstance(ranking, (list, tuple)):
        ranking = {name: i + 1 for i, name in enumerate(ranking)}
    if not isinstance(ranking, dict):
        return None
    norm = {}
    for k, v in ranking.items():
        name = _norm_value_name(k)
        if name is None:
            continue
        try:
            norm[name] = int(v)
        except (TypeError, ValueError):
            return None
    return norm if set(norm) == set(WORK_VALUES) else None


def _pref_vec(norm):
    """Canonical complete ranking dict -> preference vector summing to 1."""
    denom = float(sum(range(1, len(WORK_VALUES) + 1)))  # 21 for 6 values
    return [(7 - norm[name]) / denom for name in WORK_VALUES]


def values_preference_vector(ranking):
    """Ipsative rank of the six work values -> preference vector summing to 1.

    ranking: dict value_name -> rank r in 1..6 (1 = most important), OR an ordered
    list of the six names. Rank r maps to (7 - r) -> 6..1, /21. Raises ValueError if
    the ranking doesn't cover all six values (strict; callers wanting graceful
    degradation should use `values_fit`, which returns None instead)."""
    norm = canonical_values_ranking(ranking)
    if norm is None:
        raise ValueError("values ranking must cover all six O*NET work values")
    return _pref_vec(norm)


def values_fit(person_ranking, occ_ranking):
    """Cosine of person vs occupation ipsative value-preference vectors -> [0,1].

    Returns None (not-scorable) if EITHER ranking can't be canonicalized — an
    incomplete/misspelled person ranking degrades this occupation to interests-only
    instead of crashing the run."""
    p = canonical_values_ranking(person_ranking)
    o = canonical_values_ranking(occ_ranking)
    if p is None or o is None:
        return None
    raw = cosine_congruence(_pref_vec(p), _pref_vec(o))
    if raw is None:
        return None
    # An ipsative rank vector cannot point anywhere near the origin, so this cosine
    # has a hard FLOOR: over all 6! = 720 orderings it never drops below
    # VALUES_COS_FLOOR. Feeding that straight into bands built for a [0,1] metric
    # meant the exactly-opposite ranking still read "Moderate" — the component could
    # not report a mismatch at all. Stretch the real range onto [0,1] so "opposite"
    # lands where it belongs. VALUES_COS_FLOOR is pinned by an exhaustive test.
    return max(0.0, (raw - VALUES_COS_FLOOR) / (1.0 - VALUES_COS_FLOOR))


def traits_fit(person_traits, occ_expectations):
    """Soft trait signal in [0,1]: 1 - mean(|person - expected|) over MAPPED traits.

    person_traits / occ_expectations: dict trait_name -> value in 0..1. Only
    traits present in BOTH are used; where no defensible occupation expectation
    exists the trait is simply absent. Returns None if nothing maps.
    """
    shared = [t for t in person_traits if t in occ_expectations]
    if not shared:
        return None
    diffs = [abs(float(person_traits[t]) - float(occ_expectations[t])) for t in shared]
    return 1.0 - sum(diffs) / len(diffs)


# ------------------------------------------------------------------ blend / band / confidence
def blended_fit(interest_fit, values_fit=None, traits_fit=None, weights=None):
    """Weighted blend over PRESENT components, renormalized to sum to 1.

    Returns (score, weights_applied). Interests-only -> weight 1.0.
    Interests+values -> 0.60/0.40 (0.45/0.75, 0.30/0.75).
    """
    weights = weights or DEFAULT_WEIGHTS
    parts = {"interests": interest_fit, "values": values_fit, "traits": traits_fit}
    present = {k: v for k, v in parts.items() if v is not None}
    if not present:
        return None, {}
    wsum = sum(weights[k] for k in present)
    applied = {k: round(weights[k] / wsum, 2) for k in present}
    score = sum(weights[k] / wsum * v for k, v in present.items())
    return score, applied


def band(score):
    """Coarse band. Never a number leaves here — this is the person-facing signal."""
    if score is None:
        return "Not scorable"
    if score < BAND_LOW_MAX:
        return "Low"
    if score < BAND_MODERATE_MAX:
        return "Moderate"
    return "Strong"


def confidence(n_interest_items, values_present, traits_present, occ_from_code):
    """Confidence note that shrinks on short / partial assessments and code-only occs."""
    score = 0.0
    score += min(n_interest_items / float(FULL_INTEREST_ITEMS), 1.0) * 0.5
    score += 0.25 if values_present else 0.0
    score += 0.15 if traits_present else 0.0
    score += 0.10 if not occ_from_code else 0.0
    if score >= 0.75:
        return "higher confidence"
    if score >= 0.45:
        return "moderate confidence"
    return "low confidence — treat as a rough sketch"


# ------------------------------------------------------------------ assembler
def _score_occupation(person_vec, n_interest_items, occ,
                      person_values=None, person_traits=None,
                      occ_expectations=None, weights=None):
    """Score one occupation. Returns (person_facing_payload, raw_overall_float).

    raw_overall_float is for INTERNAL ranking only and must not be surfaced.
    """
    o_vec, from_code = occupation_interest_vector(occ)
    i_fit = cosine_congruence(person_vec, o_vec)

    v_fit = None
    if person_values is not None and occ.get("work_values"):
        v_fit = values_fit(person_values, occ["work_values"])

    t_fit = None
    if person_traits is not None and occ_expectations:
        t_fit = traits_fit(person_traits, occ_expectations)

    overall, applied = blended_fit(i_fit, v_fit, t_fit, weights)

    components = [k for k, v in (("interests", i_fit), ("values", v_fit),
                                 ("traits", t_fit)) if v is not None]
    notes = []
    if from_code:
        notes.append("occupation scored from 3-letter high-point code")

    payload = {
        "occupation": occ.get("title"),
        "onet_code": occ.get("soc_code"),
        "interest_band": band(i_fit),
        "overall_band": band(overall),
        "components_used": components,
        "weights_applied": applied,
        "confidence": confidence(n_interest_items,
                                 v_fit is not None, t_fit is not None, from_code),
        "notes": notes,
    }
    if occ.get("job_zone") is not None:
        payload["job_zone"] = occ["job_zone"]
    # raw float intentionally NOT in payload; returned separately for ranking.
    raw = overall if overall is not None else -1.0
    return payload, raw


def score_person(responses, scoring_key, occupations,
                 person_values=None, person_traits=None,
                 occ_expectations_by_soc=None, weights=None):
    """Score one person against every occupation. Returns list of payloads
    (person-facing; no raw floats), sorted best-first by the internal float."""
    person_vec, n_items = person_interest_vector(responses, scoring_key)
    occ_exp = occ_expectations_by_soc or {}
    scored = []
    for occ in occupations:
        payload, raw = _score_occupation(
            person_vec, n_items, occ,
            person_values=person_values, person_traits=person_traits,
            occ_expectations=occ_exp.get(occ.get("soc_code")), weights=weights)
        payload["data_quality"] = ("numeric-interests"
                                   if occ.get("riasec") is not None else "code-only")
        scored.append((raw, payload))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in scored]


def score_person_grouped(responses, scoring_key, occupations, **kw):
    """The honest shape of this result: TWO lists, not one.

    68 occupations carry real numeric O*NET interest ratings; 120 carry only a
    3-letter high-point code reconstructed 3-2-1. Those produce differently-shaped
    score distributions, so one shared band threshold does not mean the same thing in
    each: for the same person the numeric set came out 63% "Strong" and the code-only
    set 20%. Merging them into one ranked table made "Strong" look comparable when it
    was not. Rank and band each group on its own, and say so.

    Returns {"refused": …} | {"numeric_interests": [...], "code_only": [...], "_note": …}
    """
    disc = response_discrimination(responses, scoring_key)
    if disc < MIN_DISCRIMINATION:
        return {
            "refused": True,
            "reason": ("答案没有区分度：六个类型的得分几乎一样，说明这套回答没有指向性"
                       f"（类型间差 {disc:.3f} < {MIN_DISCRIMINATION}）。"),
            "_next": ("这不是「匹配度低」，是「测不出来」。请对方重做一次，明确区分"
                      "喜欢与不喜欢；或者直接聊他实际做过什么、什么时候最投入。"
                      "不要拿这份回答生成排名。"),
            "discrimination": round(disc, 3),
        }
    ranked = score_person(responses, scoring_key, occupations, **kw)
    return {
        "numeric_interests": [p for p in ranked if p.get("data_quality") == "numeric-interests"],
        "code_only": [p for p in ranked if p.get("data_quality") == "code-only"],
        "discrimination": round(disc, 3),
        "_note": ("两组分别排名。numeric_interests 有真实 O*NET 兴趣分；code_only 的兴趣"
                  "信号是从三字母高点码 3-2-1 反推的，置信度更低。**两组的档位不可互相"
                  "比较** —— 不要把它们并成一张表，也不要说某个 code_only 职业比某个 "
                  "numeric 职业更契合。"),
    }


def rank_occupations(responses, scoring_key, occupations, top_n=10, **kw):
    """Convenience wrapper: top-N person-facing payloads."""
    return score_person(responses, scoring_key, occupations, **kw)[:top_n]


# ------------------------------------------------------------------ title lookup
# Mode B (aspiration-job fit) starts from what the person SAYS — "产品经理", "MRI
# 算法工程师", "I want to do UX". Nothing connected those words to a SOC code, so the
# mapping happened by eyeballing a 3000-line JSON, and its failure mode was silent:
# score them against a plausible-looking wrong occupation and never mention it.
# This makes the mapping explicit, ranked, and refusable.

# Words that carry no signal for matching a job title.
_STOP = {"the", "a", "an", "of", "and", "or", "for", "in", "at", "to", "&",
         "工作", "职业", "岗位", "工程师", "师", "员", "人员", "专家", "做"}

# A small bridge from everyday words (incl. Chinese) to O*NET title vocabulary. It is
# deliberately small and visible rather than a fuzzy black box — an unmatched query
# must come back empty so the model asks, instead of quietly picking something near.
_ALIASES = {
    "产品经理": "product manager", "程序员": "programmer software developer",
    "软件工程师": "software developer", "算法": "data scientist research computer",
    "机器学习": "data scientist computer research", "人工智能": "computer research scientist",
    "数据": "data scientist statistician database", "数据分析": "data scientist operations research",
    "医生": "physician", "护士": "nurse", "老师": "teacher", "教师": "teacher",
    "律师": "lawyer", "会计": "accountant", "设计师": "designer",
    "记者": "reporter journalist", "翻译": "interpreters translators",
    "心理咨询": "counselor psychologist", "社工": "social worker",
    "厨师": "chef cook", "摄影": "photographer", "建筑师": "architect",
    "护理": "nurse", "影像": "imaging radiologic", "放射": "radiologic imaging",
    "核磁": "magnetic resonance imaging", "磁共振": "magnetic resonance imaging",
    "理疗": "physical therapist", "药剂": "pharmacist", "销售": "sales",
    "市场": "marketing market research", "人力资源": "human resources",
    "运营": "operations management", "研究员": "research scientist",
    "教授": "professor teacher postsecondary", "咨询顾问": "management analyst",
    "ux": "web digital designer", "ui": "web digital designer",
    "product manager": "management analyst project management",
}


def _tokens(s):
    s = s.lower()
    for zh, en in _ALIASES.items():
        if zh in s:
            s += " " + en
    parts = re.split(r"[^a-z0-9一-鿿]+", s)
    return {p for p in parts if p and p not in _STOP and not p.isdigit()}


def find_occupations(query, occupations, limit=8):
    """Rank shipped occupations by how well their title matches `query`.

    Returns [{soc_code, title, job_zone, has_numeric_interests, has_work_values,
    score}], best first, and **an empty list when nothing matches** — that is the
    honest answer, not a reason to reach for the nearest title.
    """
    q = _tokens(query)
    if not q:
        return []
    out = []
    for o in occupations:
        title = o.get("title", "")
        t = _tokens(title)
        if not t:
            continue
        overlap = q & t
        if not overlap:
            # allow a prefix hit so "statistic" finds "Statisticians"
            overlap = {a for a in q for b in t if len(a) > 3 and (b.startswith(a) or a.startswith(b))}
            if not overlap:
                continue
        score = len(overlap) / len(q | t)
        if query.strip().lower() == title.lower():
            score = 1.0
        out.append({
            "soc_code": o.get("soc_code"), "title": title,
            "job_zone": o.get("job_zone"),
            "has_numeric_interests": o.get("riasec") is not None,
            "has_work_values": o.get("work_values") is not None,
            "score": round(score, 3),
        })
    out.sort(key=lambda r: (-r["score"], r["title"]))
    return out[:limit]


# ------------------------------------------------------------------ data loading
def load_occupations(path=DATA_PATH_DEFAULT):
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("occupations", []), doc.get("attribution", "")


def load_scoring_key(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(DATA_PATH_DEFAULT), "assessment_items.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("scoring_key", {})


# ------------------------------------------------------------------ selftest / demo
def _selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("  PASS " if cond else "  FAIL ") + name)

    # expansion
    v = expand_highpoint_code("IRC")  # I=3,R=2,C=1 -> /6
    check("expand IRC sums to 1", abs(sum(v) - 1.0) < 1e-9)
    check("expand IRC I>R>C ordering",
          v[RIASEC_ORDER.index("I")] > v[RIASEC_ORDER.index("R")] >
          v[RIASEC_ORDER.index("C")] > 0)
    check("expand single letter 'C'", abs(sum(expand_highpoint_code("C")) - 0.5) < 1e-9)

    # ratings rescale
    check("ratings 1->0 and 7->1",
          interest_vector_from_ratings([1, 7, 1, 1, 1, 1])[0] == 0.0 and
          interest_vector_from_ratings([1, 7, 1, 1, 1, 1])[1] == 1.0)

    # cosine bounds + degenerate
    check("cosine identical == 1", abs(cosine_congruence([0, 1, 0, 0, 0, 0],
                                                         [0, 2, 0, 0, 0, 0]) - 1.0) < 1e-9)
    check("cosine orthogonal == 0", abs(cosine_congruence([1, 0, 0, 0, 0, 0],
                                                          [0, 1, 0, 0, 0, 0])) < 1e-9)
    check("cosine degenerate -> None", cosine_congruence([0, 0, 0, 0, 0, 0],
                                                         [0, 1, 0, 0, 0, 0]) is None)

    # person vector normalization (uneven item counts)
    key = {"R": [1, 2, 3, 4], "I": [5, 6, 7, 8], "A": [9, 10, 11, 12],
           "S": [13, 14, 15], "E": [16, 17, 18], "C": [19, 20, 21]}
    resp_all4 = {i: 4 for i in range(1, 22)}
    pv, n = person_interest_vector(resp_all4, key)
    check("all-max responses -> all 1.0", all(abs(x - 1.0) < 1e-9 for x in pv) and n == 21)

    # blend renormalization
    s, applied = blended_fit(0.8, 0.6, None)  # interests+values
    check("blend interests+values weights 0.60/0.40",
          applied == {"interests": 0.6, "values": 0.4})
    check("blend value 0.6*0.8+0.4*0.6", abs(s - (0.6 * 0.8 + 0.4 * 0.6)) < 1e-9)
    s1, a1 = blended_fit(0.9, None, None)
    check("interests-only weight 1.0", a1 == {"interests": 1.0} and abs(s1 - 0.9) < 1e-9)

    # bands
    check("band thresholds", band(0.54) == "Low" and band(0.6) == "Moderate"
          and band(0.9) == "Strong" and band(None) == "Not scorable")

    # confidence monotonicity
    lo = confidence(6, False, False, True)
    hi = confidence(21, True, True, False)
    check("confidence low on short+code-only", lo.startswith("low"))
    check("confidence higher on full battery", hi == "higher confidence")

    # values ipsative
    vv = values_preference_vector(list(WORK_VALUES))
    check("values vector sums to 1", abs(sum(vv) - 1.0) < 1e-9)
    check("values top == 6/21", abs(vv[0] - 6 / 21.0) < 1e-9)

    # values ranking robustness (the path #4 made live — must degrade, not crash)
    full = {"Achievement": 1, "Independence": 2, "Recognition": 3,
            "Relationships": 4, "Support": 5, "Working Conditions": 6}
    underscore = dict(full); underscore["Working_Conditions"] = underscore.pop("Working Conditions")
    check("underscore name normalizes", values_fit(underscore, full) is not None)
    incomplete = {k: full[k] for k in list(full)[:4]}  # only 4 of 6
    check("incomplete person ranking -> values_fit None (no crash)",
          values_fit(incomplete, full) is None)
    check("unknown value name -> None", canonical_values_ranking(
          {"Nope": 1, "Independence": 2, "Recognition": 3, "Relationships": 4,
           "Support": 5, "Working Conditions": 6}) is None)
    # an occupation with an incomplete work_values must not crash a scored run
    key6 = {"R": [1], "I": [2], "A": [3], "S": [4], "E": [5], "C": [6]}
    bad_occ = [{"soc_code": "x", "title": "X", "riasec": [4, 4, 4, 4, 4, 4],
                "work_values": {"Achievement": 1}}]  # malformed wv
    try:
        _ = score_person({1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4}, key6, bad_occ,
                         person_values=full)
        check("malformed occ work_values degrades (no crash)", True)
    except Exception:
        check("malformed occ work_values degrades (no crash)", False)

    # end-to-end against shipped data (if present)
    try:
        occs, attr = load_occupations()
        check("attribution present", "O*NET" in attr and "CC BY 4.0" in attr)
        # An I-leaning person should surface an I-first occupation on top.
        resp = {i: (4 if i in (5, 6, 7, 8) else 1) for i in range(1, 22)}
        top = rank_occupations(resp, key, occs, top_n=5)
        check("ranking returns payloads without raw floats",
              top and all("_score" not in p and "overall_band" in p for p in top))
        check("top occupation is Investigative-first",
              any(o["soc_code"] == p["onet_code"] and o["high_point_code"][0] == "I"
                  for p in top[:1] for o in occs))
    except FileNotFoundError:
        print("  SKIP end-to-end (occupations.json not found)")

    print("SELFTEST:", "OK" if ok else "FAILURES")
    return ok


def _demo():
    key = load_scoring_key()
    occs, attr = load_occupations()
    # An Investigative+Conventional leaning person (data-science shape).
    resp = {i: 1 for i in range(1, 22)}
    for i in (5, 6, 7, 8, 19, 20, 21):
        resp[i] = 4
    print("Attribution:", attr[:80], "...\n")
    print("Top matches (interest-only run):")
    for p in rank_occupations(resp, key, occs, top_n=8):
        print(f"  {p['overall_band']:8s} | {p['confidence']:35s} | "
              f"{p['occupation']} ({p['onet_code']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Honest career interest/values/traits matcher")
    ap.add_argument("--selftest", action="store_true", help="run internal checks")
    ap.add_argument("--demo", action="store_true", help="rank shipped occupations for a demo profile")
    ap.add_argument("--find", default=None, metavar="TITLE",
                    help="map what the person CALLS a job ('产品经理', 'MRI 技师') to the "
                         "shipped O*NET occupations, before scoring anything against it")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)
    if args.find:
        occs, _ = load_occupations()
        hits = find_occupations(args.find, occs)
        payload = {
            "query": args.find, "matches": hits,
            "_note": (
                "Confirm the mapping with the person before scoring — «你说的X，我按 O*NET "
                "的「<title>」来算，行吗?» Scoring them against a title they didn't mean is "
                "a wrong answer that looks right."
                if hits else
                "NO MATCH in the 188 shipped occupations. Do NOT substitute the nearest "
                "title. Say the role isn't in the dataset, ask which of the shipped ones "
                "is closest in day-to-day WORK (not job title), or give an interests-only "
                "read with no occupation congruence at all."),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if not hits:
                print(f"no match for {args.find!r} among the 188 shipped occupations.")
            for h in hits:
                flags = ("numeric-interests" if h["has_numeric_interests"] else "code-only") + \
                        (" +values" if h["has_work_values"] else "")
                print(f"  {h['score']:.2f}  {h['title']} ({h['soc_code']}) "
                      f"[zone {h['job_zone']}, {flags}]")
            print("\n" + payload["_note"])
        raise SystemExit(0)
    if args.demo:
        _demo()
    else:
        ap.print_help()
