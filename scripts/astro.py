#!/usr/bin/env python3
"""
astro.py — REAL Western-astrology positions for a daily reading.

Honesty note: this computes ACTUAL astronomical positions (Swiss Ephemeris via
pyswisseph, offline Moshier mode — no data files, no network). The positions,
signs, retrogrades and aspect geometry it returns are *facts of the sky*; what any
of it "means" is a reflective/cultural lens, labeled as such by the reading — never
a scientific prediction. It fabricates nothing: if birth time is unknown, it omits
the Moon/rising rather than guessing.

Usage:
  python3 astro.py --date 1993-04-12 [--time 16:00] --on-date today --format json
  python3 astro.py --date 1993-04-12 --on-date 2026-07-18 --format text
"""
import argparse
import datetime
import json
import subprocess
import sys


def _ensure(pkg, mod=None):
    mod = mod or pkg
    try:
        return __import__(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", pkg], check=True)
        return __import__(mod)


swe = _ensure("pyswisseph", "swisseph")

SIGNS = ["白羊", "金牛", "双子", "巨蟹", "狮子", "处女",
         "天秤", "天蝎", "射手", "摩羯", "水瓶", "双鱼"]
SIGN_EN = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
           "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
SIGN_ELEMENT = {  # 星座四元素 (reflective flavor)
    "白羊": "火", "狮子": "火", "射手": "火", "金牛": "土", "处女": "土", "摩羯": "土",
    "双子": "风", "天秤": "风", "水瓶": "风", "巨蟹": "水", "天蝎": "水", "双鱼": "水",
}
PLANETS = [("太阳", swe.SUN), ("月亮", swe.MOON), ("水星", swe.MERCURY),
           ("金星", swe.VENUS), ("火星", swe.MARS), ("木星", swe.JUPITER),
           ("土星", swe.SATURN)]
# Full natal set = the daily 7 + the outer 3 + the lunar nodes (reflective layer).
NATAL_PLANETS = PLANETS + [("天王星", swe.URANUS), ("海王星", swe.NEPTUNE),
                           ("冥王星", swe.PLUTO), ("北交点", swe.TRUE_NODE)]
ASPECTS = [(0, "合", 7), (60, "六合(和谐)", 5), (90, "刑(张力)", 6),
           (120, "拱(顺遂)", 6), (180, "冲(对立)", 7)]


def _sign(lon):
    return SIGNS[int(lon // 30) % 12]


def _jd(dt):
    return swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute / 60.0)


def _lon_speed(jd, planet):
    r = swe.calc_ut(jd, planet)[0]
    return r[0] % 360.0, r[3]  # longitude, daily speed (neg = retrograde)


def _angle(a, b):
    d = abs((a - b) % 360.0)
    return min(d, 360 - d)


def compute(birth_date, birth_time, on_date):
    by, bm, bd = (int(x) for x in birth_date.split("-"))
    hh, mm = ((int(x) for x in birth_time.split(":")) if birth_time else (12, 0))
    time_known = birth_time is not None
    # natal (UTC approx; sun/most planets barely move intra-day)
    natal_jd = swe.julday(by, bm, bd, hh + mm / 60.0)
    sun_natal = _lon_speed(natal_jd, swe.SUN)[0]
    moon_natal = _lon_speed(natal_jd, swe.MOON)[0] if time_known else None

    tjd = _jd(datetime.datetime(on_date.year, on_date.month, on_date.day, 12, 0))
    today = {}
    for name, pl in PLANETS:
        lon, spd = _lon_speed(tjd, pl)
        today[name] = {"sign": _sign(lon), "lon": round(lon, 2),
                       "retrograde": spd < 0}

    # transits from fast movers to the natal Sun (and Moon if known)
    transits = []
    targets = [("本命太阳", sun_natal)]
    if moon_natal is not None:
        targets.append(("本命月亮", moon_natal))
    for tname, tlon in targets:
        for pname, pl in [("太阳", swe.SUN), ("月亮", swe.MOON), ("水星", swe.MERCURY),
                          ("金星", swe.VENUS), ("火星", swe.MARS)]:
            plon = _lon_speed(tjd, pl)[0]
            ang = _angle(plon, tlon)
            for deg, label, orb in ASPECTS:
                if abs(ang - deg) <= orb:
                    transits.append({"from": f"流{pname}", "to": tname,
                                     "aspect": label, "orb": round(abs(ang - deg), 1)})
                    break

    retros = [n for n in ("水星", "金星", "火星", "木星", "土星") if today[n]["retrograde"]]

    return {
        "system": "Western astrology (real ephemeris, Swiss/Moshier)",
        "date": on_date.isoformat(),
        "sun_sign": _sign(sun_natal),
        "sun_sign_en": SIGN_EN[int(sun_natal // 30) % 12],
        "sun_element": SIGN_ELEMENT[_sign(sun_natal)],
        "moon_sign_natal": (_sign(moon_natal) if moon_natal is not None else None),
        "time_known": time_known,
        "today_moon_sign": today["月亮"]["sign"],
        "today_planets": today,
        "retrogrades": retros,
        "transits_to_natal": transits,
        "disclaimer": ("以上为真实天文位置(可复现的事实);星座/相位的『意义』是文化性的"
                       "反思视角,非科学预测。"),
    }


def natal(birth_date, birth_time=None, lat=None, lon=None, tz_offset=None):
    """Compute a REAL natal chart: planets-in-signs + natal aspects, and the
    Ascendant/houses ONLY when birth time + place + timezone are all known.

    Everything returned is astronomical fact; meaning is a reflective lens (labeled
    by the caller). It fabricates nothing — it omits, with a stated reason, any part
    that the person's birth data can't support:
      * `time_known=False`  -> no Ascendant/houses; the Moon is flagged approximate
        (it moves ~12-15°/day, so its sign can be wrong without a birth time).
      * `tz_offset` unknown -> birth time is treated as UT and flagged; fine for the
        slow planets' signs, unreliable for the Ascendant (which we then omit).
      * `lat`/`lon` unknown -> no houses/Ascendant (they are place-specific).

    tz_offset is hours east of UTC (e.g. +8 for China, +1 for NL winter). Returns a
    dict; `caveats` lists every honest limitation so the reading can state them.
    """
    by, bm, bd = (int(x) for x in birth_date.split("-"))
    time_known = birth_time is not None
    hh, mm = ((int(x) for x in birth_time.split(":")) if time_known else (12, 0))
    caveats = []
    # Local -> UT. Without tz we treat the clock time as UT (flagged below, after
    # `planets` exists — the Moon moves too fast to trust the sign without a real UT).
    no_tz_with_time = tz_offset is None and time_known
    if tz_offset is None:
        ut_hour = hh + mm / 60.0
    else:
        ut_hour = hh + mm / 60.0 - float(tz_offset)
    jd = swe.julday(by, bm, bd, ut_hour)

    planets = {}
    for name, pl in NATAL_PLANETS:
        lonp, spd = _lon_speed(jd, pl)
        entry = {"sign": _sign(lonp), "sign_en": SIGN_EN[int(lonp // 30) % 12],
                 "deg_in_sign": round(lonp % 30, 2), "lon": round(lonp, 2)}
        if name != "北交点":  # the node is always ~retrograde by nature; not a flag
            entry["retrograde"] = spd < 0
        planets[name] = entry
    if not time_known:
        planets["月亮"]["approximate"] = True
        caveats.append("未提供出生时间:月亮星座按当日正午估算,可能跨座;上升/宫位无法计算。"
                       "太阳及慢速行星星座一般不受影响。")
    elif no_tz_with_time:
        planets["月亮"]["approximate"] = True
        caveats.append("未提供出生地时区,出生时间按 UT 处理:太阳及慢速行星星座可靠,"
                       "但月亮(移动快)可能差一个星座、且上升/宫位不可靠(已省略)。"
                       "给出时区可修正。")

    # natal planet-planet aspects (major only), each pair once
    names = [n for n, _ in NATAL_PLANETS if n != "北交点"]
    aspects = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = planets[names[i]]["lon"], planets[names[j]]["lon"]
            ang = _angle(a, b)
            for deg, label, orb in ASPECTS:
                if abs(ang - deg) <= orb:
                    aspects.append({"a": names[i], "b": names[j], "aspect": label,
                                    "orb": round(abs(ang - deg), 1)})
                    break

    # Ascendant / houses: need time + place + a real UT (tz). Else omit honestly.
    ascendant = houses = midheaven = None
    if time_known and lat is not None and lon is not None and tz_offset is not None:
        try:
            cusps, ascmc = swe.houses(jd, float(lat), float(lon), b"P")  # Placidus
            asc_lon, mc_lon = ascmc[0] % 360.0, ascmc[1] % 360.0
            ascendant = {"sign": _sign(asc_lon), "sign_en": SIGN_EN[int(asc_lon // 30) % 12],
                         "deg_in_sign": round(asc_lon % 30, 2)}
            midheaven = {"sign": _sign(mc_lon), "deg_in_sign": round(mc_lon % 30, 2)}
            houses = [{"house": k + 1, "sign": _sign(c % 360.0),
                       "deg_in_sign": round((c % 360.0) % 30, 2)}
                      for k, c in enumerate(list(cusps)[:12])]
        except Exception as e:  # high-latitude Placidus failure etc. — omit, don't fake
            caveats.append(f"宫位计算失败({e});已省略上升/宫位,行星星座与相位不受影响。")
    elif time_known and (lat is None or lon is None):
        caveats.append("已知出生时间但缺出生地经纬度:上升/中天/宫位与地点相关,无法计算,已省略。")

    return {
        "system": "Western natal chart (real ephemeris, Swiss/Moshier)",
        "birth_date": birth_date,
        "birth_time": birth_time,
        "time_known": time_known,
        "tz_offset": tz_offset,
        "sun_sign": planets["太阳"]["sign"],
        "sun_sign_en": planets["太阳"]["sign_en"],
        "sun_element": SIGN_ELEMENT[planets["太阳"]["sign"]],
        "moon_sign": planets["月亮"]["sign"],
        "planets": planets,
        "aspects": aspects,
        "ascendant": ascendant,
        "midheaven": midheaven,
        "houses": houses,
        "caveats": caveats,
        "disclaimer": ("以上行星经度/星座/相位为真实天文事实(可复现);其『意义』是文化性的"
                       "反思视角,非科学预测,也非命运判定。"),
    }


def _natal_text(r):
    L = [f"本命盘 ({r['birth_date']}{' ' + r['birth_time'] if r['birth_time'] else ' 时间未知'})",
         f"  太阳 {r['sun_sign']}座 ({r['sun_element']}象) · 月亮 {r['moon_sign']}座"
         + ("(近似)" if r["planets"]["月亮"].get("approximate") else "")]
    if r["ascendant"]:
        L.append(f"  上升 {r['ascendant']['sign']}座 · 中天 {r['midheaven']['sign']}座")
    L.append("  行星落座:")
    for name in [n for n, _ in NATAL_PLANETS]:
        p = r["planets"][name]
        rx = "逆" if p.get("retrograde") else ""
        L.append(f"    {name}: {p['sign']}座 {p['deg_in_sign']}°{rx}")
    if r["aspects"]:
        L.append("  主要相位:")
        for a in r["aspects"][:8]:
            L.append(f"    {a['a']} {a['aspect']} {a['b']} (orb {a['orb']}°)")
    for c in r["caveats"]:
        L.append("  ⚠ " + c)
    L.append("\n" + r["disclaimer"])
    return "\n".join(L)


def _text(r):
    L = [f"星座: {r['sun_sign']}座 ({r['sun_sign_en']}, {r['sun_element']}象)"]
    L.append(f"今日({r['date']})月亮在 {r['today_moon_sign']}座")
    if r["retrogrades"]:
        L.append("逆行中: " + "、".join(x + "逆" for x in r["retrogrades"]))
    if r["transits_to_natal"]:
        L.append("今日对你本命的相位:")
        for t in r["transits_to_natal"][:5]:
            L.append(f"  {t['from']} {t['aspect']} {t['to']} (orb {t['orb']}°)")
    L.append("\n" + r["disclaimer"])
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="birth date YYYY-MM-DD")
    ap.add_argument("--time", default=None, help="birth time HH:MM (omit if unknown)")
    ap.add_argument("--on-date", default="today", help="daily mode: YYYY-MM-DD or 'today'")
    ap.add_argument("--natal", action="store_true",
                    help="compute the full natal chart instead of the daily reading")
    ap.add_argument("--lat", type=float, default=None, help="natal: birth latitude (for houses/ascendant)")
    ap.add_argument("--lon", type=float, default=None, help="natal: birth longitude (for houses/ascendant)")
    ap.add_argument("--tz", type=float, default=None,
                    help="natal: birth-place UTC offset in hours (e.g. 8, 1, -5) — needed for ascendant")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()
    try:
        if args.natal:
            r = natal(args.date, args.time, lat=args.lat, lon=args.lon, tz_offset=args.tz)
            print(_natal_text(r) if args.format == "text"
                  else json.dumps(r, ensure_ascii=False, indent=2))
            return
        on_date = (datetime.date.today() if args.on_date == "today"
                   else datetime.date.fromisoformat(args.on_date))
        r = compute(args.date, args.time, on_date)
    except (ValueError, TypeError) as e:
        print(json.dumps({"ok": False, "error": f"bad input: {e}"}, ensure_ascii=False))
        sys.exit(2)
    print(_text(r) if args.format == "text" else json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
