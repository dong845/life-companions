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
    return cosine_congruence(_pref_vec(p), _pref_vec(o))


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
        scored.append((raw, payload))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in scored]


def rank_occupations(responses, scoring_key, occupations, top_n=10, **kw):
    """Convenience wrapper: top-N person-facing payloads."""
    return score_person(responses, scoring_key, occupations, **kw)[:top_n]


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
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)
    if args.demo:
        _demo()
    else:
        ap.print_help()
