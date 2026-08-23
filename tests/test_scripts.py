#!/usr/bin/env python3
"""
Regression tests for the life-companion scripts.

    python3 tests/test_scripts.py            # or: python3 -m unittest discover tests

Plain unittest on purpose — no pytest, no network, no fixtures to install, so this
runs identically under any agent on any machine. Every test here corresponds to a
defect that actually shipped and passed every other check, or to a boundary the
skill's honesty claims depend on.

The skill's own rule is "compute honestly": these are the tests that make the
computed half falsifiable instead of merely asserted.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)


def run(script, *args, home=None, expect_ok=True):
    """Run a skill script and return (returncode, stdout, stderr)."""
    env = dict(os.environ)
    if home:
        env["COMPANION_HOME"] = home
    env["LIFE_COMPANION_NO_AUTOINSTALL"] = "1"   # tests never reach for the network
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                       capture_output=True, text=True, env=env)
    if expect_ok and r.returncode not in (0, 1, 2, 3):   # 3 = refused (consent gate)
        raise AssertionError(f"{script} {args} exited {r.returncode}\n{r.stderr}")
    return r.returncode, r.stdout, r.stderr


def jrun(script, *args, home=None):
    _, out, _ = run(script, *args, home=home)
    return json.loads(out)


class HomeCase(unittest.TestCase):
    """Each test gets a throwaway COMPANION_HOME — never touches the real one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        run("companion.py", "init", home=self.home)

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
class TestJournalIntegrity(HomeCase):
    """The mood value feeds `trend`, which is presented to the person as a FACT."""

    def _grant_mood(self):
        run("companion.py", "consent", "--set", "mood=yes", home=self.home)

    def test_mood_out_of_range_is_rejected(self):
        self._grant_mood()
        for bad in ("99", "-3", "11"):
            r = jrun("companion.py", "add-entry", "--text", "x", "--mood", bad, home=self.home)
            self.assertFalse(r["ok"], f"mood={bad} should be rejected")
            self.assertIn("0..10", r["error"])

    def test_mood_bounds_are_inclusive(self):
        self._grant_mood()
        for good in ("0", "10", "5"):
            r = jrun("companion.py", "add-entry", "--text", "x", "--mood", good, home=self.home)
            self.assertTrue(r["ok"], f"mood={good} should be accepted")
            self.assertEqual(r["mood"], int(good))

    def test_trend_cannot_be_poisoned(self):
        self._grant_mood()
        for m in ("6", "7", "5"):
            run("companion.py", "add-entry", "--text", "d", "--mood", m, home=self.home)
        run("companion.py", "add-entry", "--text", "d", "--mood", "999", home=self.home)
        t = jrun("companion.py", "trend", "--days", "30", home=self.home)
        self.assertLessEqual(t["mood_avg"], 10)
        self.assertGreaterEqual(t["mood_avg"], 0)

    def test_ungranted_mood_is_dropped_LOUDLY(self):
        # consent.mood is None at init: mood must not be stored, and the caller must
        # be told — a silent drop makes the model claim it logged a mood it didn't.
        r = jrun("companion.py", "add-entry", "--text", "今天还行", "--mood", "7",
                 home=self.home)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["mood"])
        self.assertIn("dropped", r)
        self.assertTrue(any("consent" in d for d in r["dropped"]))

    def test_granted_mood_is_stored_and_not_reported_dropped(self):
        self._grant_mood()
        r = jrun("companion.py", "add-entry", "--text", "好", "--mood", "7", home=self.home)
        self.assertEqual(r["mood"], 7)
        self.assertNotIn("dropped", r)

    def test_entry_text_is_saved_even_when_mood_is_dropped(self):
        jrun("companion.py", "add-entry", "--text", "记住这句", "--mood", "7", home=self.home)
        _, out, _ = run("companion.py", "journal", home=self.home)
        self.assertIn("记住这句", out)


class TestContinuityThreads(HomeCase):
    """An action-thread that duplicates on edit makes `followups` nag forever."""

    THREAD = {"thread": "换工作", "action": "投3个岗", "opened": "2026-08-10",
              "last_nudged": None, "status": "open"}

    def _threads(self):
        import yaml
        with open(os.path.join(self.home, "state", "continuity.yaml"), encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("open_threads") or []

    def test_editing_a_thread_updates_it_in_place(self):
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [self.THREAD]}), home=self.home)
        edited = dict(self.THREAD, last_nudged="2026-08-22")
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [edited]}), home=self.home)
        threads = self._threads()
        self.assertEqual(len(threads), 1, f"thread duplicated: {threads}")
        self.assertEqual(threads[0]["last_nudged"], "2026-08-22")

    def test_a_different_thread_still_appends(self):
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [self.THREAD]}), home=self.home)
        other = {"thread": "体检", "action": "约个时间", "opened": "2026-08-20",
                 "last_nudged": None, "status": "open"}
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [other]}), home=self.home)
        self.assertEqual(len(self._threads()), 2)

    def test_resending_identical_thread_is_a_noop(self):
        for _ in range(3):
            run("companion.py", "continuity", "--merge-json",
                json.dumps({"open_threads": [self.THREAD]}), home=self.home)
        self.assertEqual(len(self._threads()), 1)

    def test_closing_a_thread_removes_it_from_followups(self):
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [self.THREAD]}), home=self.home)
        self.assertEqual(jrun("companion.py", "followups", home=self.home)["count"], 1)
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"open_threads": [dict(self.THREAD, status="done")]}), home=self.home)
        self.assertEqual(jrun("companion.py", "followups", home=self.home)["count"], 0)

    def test_keyless_list_items_still_accrete(self):
        # relationship incidents have no stable key — history must keep growing
        run("companion.py", "consent", "--set", "relationships=yes", home=self.home)
        run("companion.py", "cache", "--module", "relationships", "--merge-json",
            json.dumps({"people": {"A": {"patterns": ["pursue-withdraw"]}}}), home=self.home)
        run("companion.py", "cache", "--module", "relationships", "--merge-json",
            json.dumps({"people": {"A": {"patterns": ["stonewalling"]}}}), home=self.home)
        import yaml
        with open(os.path.join(self.home, "state", "modules", "relationships.yaml"),
                  encoding="utf-8") as f:
            c = yaml.safe_load(f) or {}
        self.assertEqual(len(c["people"]["A"]["patterns"]), 2)


    def test_two_incidents_on_the_same_day_both_survive(self):
        # `date` must NOT act as an identity key — a couple can have two rows on one
        # day, and upserting on the date would silently delete one of them.
        run("companion.py", "consent", "--set", "relationships=yes", home=self.home)
        for gist in ("早上因为洗碗吵了", "晚上又聊崩了"):
            run("companion.py", "cache", "--module", "relationships", "--merge-json",
                json.dumps({"people": {"A": {"incidents": [
                    {"date": "2026-08-20", "gist": gist, "lens": "criticism"}]}}}),
                home=self.home)
        import yaml
        with open(os.path.join(self.home, "state", "modules", "relationships.yaml"),
                  encoding="utf-8") as f:
            c = yaml.safe_load(f) or {}
        self.assertEqual(len(c["people"]["A"]["incidents"]), 2)


class TestBrief(HomeCase):
    """`brief` is the every-turn protocol in one call — it must be self-sufficient."""

    def test_uninitialised_home_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as fresh:
            b = jrun("companion.py", "brief", home=fresh)
            self.assertFalse(b["initialized"])
            self.assertIn("_next", b)

    def test_brief_carries_everything_the_protocol_needs(self):
        run("companion.py", "consent", "--set", "mood=yes", home=self.home)
        run("companion.py", "set-profile", "--merge-json",
            json.dumps({"identity": {"name": "小明", "locale": "zh"}}), home=self.home)
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"rolling_summary": "在准备面试",
                        "open_threads": [{"thread": "面试", "action": "投简历",
                                          "opened": "2026-08-01", "last_nudged": None,
                                          "status": "open"}]}), home=self.home)
        run("companion.py", "add-entry", "--text", "走了一圈", "--mood", "6", home=self.home)
        b = jrun("companion.py", "brief", home=self.home)
        self.assertTrue(b["initialized"])
        self.assertEqual(b["profile"]["identity"]["name"], "小明")
        self.assertEqual(b["continuity"]["rolling_summary"], "在准备面试")
        self.assertEqual(len(b["followups_due"]), 1)
        self.assertEqual(b["journal"]["entries"], 1)
        self.assertIn("mood", b["consent"])

    def test_recent_crisis_entry_is_surfaced(self):
        run("companion.py", "add-entry", "--text", "撑不下去了", "--crisis", home=self.home)
        b = jrun("companion.py", "brief", home=self.home)
        self.assertIn("_crisis_recent", b)


class TestConsentAndForget(HomeCase):
    def test_forget_birth_actually_deletes(self):
        run("companion.py", "consent", "--set", "birth=yes", home=self.home)
        run("companion.py", "set-profile", "--merge-json",
            json.dumps({"birth": {"date": "1993-04-12", "place": "Beijing, CN"}}),
            home=self.home)
        run("companion.py", "forget", "--birth", home=self.home)
        prof = jrun("companion.py", "read-profile", "--json", home=self.home)
        self.assertIsNone(prof["birth"]["date"])
        with open(os.path.join(self.home, "profile.yaml"), encoding="utf-8") as f:
            self.assertNotIn("1993-04-12", f.read())


class TestBaZi(unittest.TestCase):
    """The 立春 boundary is where a chart is genuinely uncertain — and where the
    engine and its cross-check are guaranteed to disagree for a benign reason."""

    def chart(self, *args):
        return jrun("bazi.py", *args)

    def test_known_chart_is_stable(self):
        c = self.chart("--date", "1993-04-12", "--time", "07:35", "--gender", "m",
                       "--format", "json")["computed"]
        self.assertEqual(c["pillars"]["year"]["ganzhi"], "癸酉")
        self.assertEqual(c["pillars"]["month"]["ganzhi"], "丙辰")
        self.assertEqual(c["pillars"]["day"]["ganzhi"], "癸亥")
        self.assertEqual(c["day_master"]["gan"], "癸")

    def test_lichun_boundary_uses_the_exact_moment(self):
        # 立春 1993 fell at 03:37 on Feb 4 → 00:30 belongs to the PREVIOUS year pillar.
        before = self.chart("--date", "1993-02-04", "--time", "00:30", "--gender", "m",
                            "--format", "json")
        after = self.chart("--date", "1993-02-04", "--time", "06:00", "--gender", "m",
                           "--format", "json")
        self.assertEqual(before["computed"]["pillars"]["year"]["ganzhi"], "壬申")
        self.assertEqual(after["computed"]["pillars"]["year"]["ganzhi"], "癸酉")

    def test_lichun_proximity_is_surfaced_as_an_ambiguity(self):
        r = self.chart("--date", "1993-02-04", "--time", "00:30", "--gender", "m",
                       "--format", "json")
        self.assertTrue(any("立春" in a for a in r["ambiguities"]),
                        "a birth 3h from 立春 must be flagged; the year pillar hinges on it")

    def test_benign_cross_check_disagreement_is_explained_not_alarming(self):
        # sxtwl is date-only, so on the 立春 day it CANNOT agree. That must be labelled
        # as expected, and must NOT raise a 'chart is unreliable' ambiguity.
        r = self.chart("--date", "1993-02-04", "--time", "00:30", "--gender", "m",
                       "--format", "json")
        x = r["computed"]["cross_check_sxtwl"]
        if not x.get("available"):
            self.skipTest("sxtwl not installed")
        self.assertIs(x["agrees"], False)
        self.assertIn("expected", x.get("_disagreement", ""))
        self.assertFalse(any("交叉核验不一致" in a for a in r["ambiguities"]))

    def test_ordinary_date_agrees_and_is_quiet(self):
        r = self.chart("--date", "1993-04-12", "--time", "07:35", "--gender", "m",
                       "--tz", "Asia/Shanghai", "--format", "json")
        x = r["computed"]["cross_check_sxtwl"]
        if x.get("available"):
            self.assertIs(x["agrees"], True)
        self.assertEqual(r["ambiguities"], [])


class TestBaZiCurrentDecade(unittest.TestCase):
    """`is_current` drives the whole 分阶段 reading, so getting it wrong is a ten-year
    error, not an off-by-one. It used to come from `today.year - birth_year`, which is
    a year-difference, not an age: before the birthday it reads one too high and pushes
    anyone sitting on a 大运 boundary into the next decade."""

    def test_age_is_birthday_aware(self):
        import bazi, datetime
        today = datetime.date.today()
        for y, m, d in ((1998, 11, 16), (2002, 12, 1), (1994, 10, 5), (1993, 4, 12)):
            got = bazi.compute(f"{y}-{m:02d}-{d:02d}", "10:00", "m")["computed"]["current_age_approx"]
            want = today.year - y - ((today.month, today.day) < (m, d))
            self.assertEqual(got, want, f"{y}-{m}-{d}")

    def test_current_decade_matches_the_real_age_across_a_sweep(self):
        import bazi, datetime
        today = datetime.date.today()
        wrong = []
        for m, d in ((11, 16), (12, 1), (10, 5), (3, 16), (6, 20)):
            for y in range(1975, 2006, 3):
                age = today.year - y - ((today.month, today.day) < (m, d))
                ps = bazi.compute(f"{y}-{m:02d}-{d:02d}", "10:00", "m")["computed"]["luck_pillars"]["pillars"]
                marked = [p for p in ps if p["is_current"]]
                correct = [p for p in ps if p["start_age"] <= age <= p["end_age"]]
                if marked and correct and marked[0]["ganzhi"] != correct[0]["ganzhi"]:
                    wrong.append((y, m, d, marked[0]["ganzhi"], correct[0]["ganzhi"]))
        self.assertEqual(wrong, [], f"{len(wrong)} charts marked the wrong decade")

    def test_unknown_hour_says_it_also_blurs_qiyun(self):
        r = jrun("bazi.py", "--date", "1993-04-12", "--gender", "m", "--format", "json")
        self.assertTrue(any("起运" in a for a in r["ambiguities"]), r["ambiguities"])


class TestBaZiTimezone(unittest.TestCase):
    """節氣 are absolute astronomical instants and lunar-python resolves them against
    China Standard Time. A birth outside UTC+8 therefore has to be moved into that
    frame or the year/month pillar can be wrong — and it was, silently, while the
    ambiguity text confidently described the WRONG side of the boundary."""

    def chart(self, *a):
        return jrun("bazi.py", "--gender", "m", "--format", "json", *a)

    def test_overseas_birth_just_after_lichun_gets_the_right_year_pillar(self):
        # 立春 1993 = 03:37 Beijing on Feb 4 = 20:37 Feb 3 in Amsterdam.
        # 21:00 local on Feb 3 is therefore AFTER it.
        r = self.chart("--date", "1993-02-03", "--time", "21:00",
                       "--tz", "Europe/Amsterdam")
        self.assertEqual(r["computed"]["pillars"]["year"]["ganzhi"], "癸酉")
        self.assertTrue(any("之后" in a and "立春" in a for a in r["ambiguities"]),
                        r["ambiguities"])

    def test_day_and_hour_pillars_stay_on_the_local_clock(self):
        # 21:00 local is 亥时 wherever you are; it must NOT become Beijing's 04:00
        r = self.chart("--date", "1993-02-03", "--time", "21:00",
                       "--tz", "Europe/Amsterdam")
        self.assertEqual(r["computed"]["pillars"]["hour"]["zhi"], "亥")

    def test_china_births_are_completely_unchanged(self):
        for args in (["--date", "1993-04-12", "--time", "07:35"],
                     ["--date", "1995-08-30", "--time", "16:00"],
                     ["--date", "1993-02-04", "--time", "00:30"],
                     ["--date", "1993-04-12"]):
            def gz(extra):
                p = self.chart(*(args + extra))["computed"]["pillars"]
                return [v["ganzhi"] if v else None for v in p.values()]
            self.assertEqual(gz([]), gz(["--tz", "Asia/Shanghai"]), args)

    def test_omitting_tz_discloses_the_assumption_instead_of_hiding_it(self):
        r = self.chart("--date", "1993-04-12", "--time", "07:35")
        self.assertTrue(any("未提供出生地时区" in a for a in r["ambiguities"]))
        self.assertIn("ASSUMED", r["computed"]["input"]["conventions"]["jieqi_frame"])

    def test_standard_meridian_follows_the_birthplace_not_china(self):
        r = self.chart("--date", "1993-07-15", "--time", "14:00",
                       "--tz", "Europe/Amsterdam", "--lon", "4.90", "--true-solar-time")
        self.assertEqual(r["computed"]["input"]["conventions"]["standard_meridian"], 30.0)

    def test_numeric_offset_is_accepted_too(self):
        a = self.chart("--date", "1993-02-03", "--time", "21:00", "--tz", "Europe/Amsterdam")
        b = self.chart("--date", "1993-02-03", "--time", "21:00", "--tz", "1")
        self.assertEqual(a["computed"]["pillars"]["year"]["ganzhi"],
                         b["computed"]["pillars"]["year"]["ganzhi"])

    def test_bad_tz_is_rejected_clearly(self):
        code, _, err = run("bazi.py", "--date", "1993-04-12", "--gender", "m",
                           "--tz", "Mars/Olympus", expect_ok=False)
        self.assertNotEqual(code, 0)

    def test_zishi_rule_changes_the_day_pillar(self):
        late = self.chart("--date", "1993-04-12", "--time", "23:30", "--gender", "m",
                          "--format", "json")
        early = self.chart("--date", "1993-04-12", "--time", "23:30", "--gender", "m",
                           "--early-zishi", "--format", "json")
        self.assertNotEqual(late["computed"]["pillars"]["day"]["ganzhi"],
                            early["computed"]["pillars"]["day"]["ganzhi"])
        self.assertTrue(any("子时" in a for a in late["ambiguities"]))

    def test_unknown_time_omits_the_hour_pillar_and_says_so(self):
        r = self.chart("--date", "1993-04-12", "--gender", "f", "--format", "json")
        self.assertIsNone(r["computed"]["pillars"]["hour"])
        self.assertTrue(any("时刻未知" in a for a in r["ambiguities"]))

    def test_bad_input_fails_cleanly(self):
        _, out, _ = run("bazi.py", "--date", "1993-13-45", "--gender", "m", "--format", "json")
        self.assertFalse(json.loads(out)["ok"])

    def test_strength_is_labelled_heuristic_not_fact(self):
        r = self.chart("--date", "1993-04-12", "--time", "07:35", "--gender", "m",
                       "--format", "json")
        self.assertIn("heuristic", r)
        self.assertIn("strength", r["heuristic"])
        self.assertNotIn("strength", r["computed"])


class TestTimezoneResolution(unittest.TestCase):
    """identity.timezone decides daily timing AND which country's crisis line the
    person is offered, so a confident wrong answer is worse than no answer."""

    def resolve(self, q):
        import companion
        return [c["timezone"] for c in companion.resolve_timezone(q)]

    def test_resolves_cities_in_both_languages(self):
        for q, expect in [("柏林", "Europe/Berlin"), ("Berlin", "Europe/Berlin"),
                          ("纽约", "America/New_York"), ("New York", "America/New_York"),
                          ("台北", "Asia/Taipei"), ("Kolkata", "Asia/Kolkata"),
                          ("Auckland", "Pacific/Auckland")]:
            self.assertIn(expect, self.resolve(q), q)

    def test_handles_diacritics_either_way(self):
        # the person spells their own city with its accents; the zone name has none
        for q in ("São Paulo", "Sao Paulo"):
            self.assertIn("America/Sao_Paulo", self.resolve(q), q)
        for q in ("Zürich", "Zurich"):
            self.assertIn("Europe/Zurich", self.resolve(q), q)

    def test_country_words_work_too(self):
        self.assertIn("Europe/Berlin", self.resolve("Germany"))
        self.assertIn("Asia/Shanghai", self.resolve("中国"))

    def test_refuses_rather_than_defaulting(self):
        for q in ("瓦坎达", "", "   ", "asdfghjkl"):
            self.assertEqual(self.resolve(q), [], q)
        out = jrun("companion.py", "resolve-tz", "瓦坎达")
        self.assertEqual(out["candidates"], [])
        self.assertIn("Do NOT guess", out["_note"])


class TestOnboardingForm(unittest.TestCase):
    """The form is the PREFERRED onboarding path, so what it writes is the profile
    most people get. It used to hard-code two countries and drop everyone else's city."""

    def _submit(self, fields, port):
        import urllib.request, urllib.parse, subprocess, time, json as _json
        home = tempfile.mkdtemp()
        run("companion.py", "init", home=home)
        env = dict(os.environ, COMPANION_HOME=home, LIFE_COMPANION_NO_AUTOINSTALL="1")
        srv = subprocess.Popen(
            [sys.executable, os.path.join(SCRIPTS, "form_server.py"), "--form", "onboarding",
             "--no-open", "--port", str(port), "--timeout", "25"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
                    break
                except Exception:
                    time.sleep(0.1)
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/submit",
                data=urllib.parse.urlencode(fields).encode()).read()
            out = srv.communicate(timeout=20)[0]
        finally:
            srv.kill()
        line = [l for l in out.splitlines() if l.startswith("SUBMITTED ")]
        return _json.loads(line[0][len("SUBMITTED "):]) if line else {}, home

    def test_a_city_outside_the_quick_picks_still_lands(self):
        summary, home = self._submit({
            "name": "Ana", "locale": "en", "region": "other", "city": "São Paulo",
            "tone": "warm-direct", "mood_consent": "on"}, 8841)
        self.assertEqual(summary.get("timezone"), "America/Sao_Paulo")
        import yaml
        with open(os.path.join(home, "profile.yaml"), encoding="utf-8") as f:
            prof = yaml.safe_load(f)
        self.assertEqual(prof["identity"]["timezone"], "America/Sao_Paulo")
        self.assertEqual(prof["identity"]["location"], "São Paulo")

    def test_form_reports_what_it_could_not_finish(self):
        summary, _ = self._submit({
            "name": "B", "locale": "zh", "region": "cn", "tone": "concise",
            "birth_consent": "on", "birth_date": "1993-04-12", "birth_time": "07:35",
            "birth_place": "Beijing, CN"}, 8842)
        todo = " ".join(summary.get("todo", []))
        self.assertIn("lat", todo)       # coords still needed for the Ascendant
        self.assertIn("性别", todo)       # needed for 大运 direction

    def test_unrecognisable_place_is_reported_not_guessed(self):
        summary, _ = self._submit({
            "name": "C", "locale": "zh", "region": "other", "city": "瓦坎达",
            "tone": "concise"}, 8843)
        self.assertIsNone(summary.get("timezone"))
        self.assertTrue(any("时区" in t for t in summary.get("todo", [])))


class TestAstro(unittest.TestCase):
    """The profile stores an IANA zone; the script used to demand a float."""

    def test_iana_zone_and_numeric_offset_agree(self):
        a = jrun("astro.py", "--date", "1993-04-12", "--time", "07:35", "--natal",
                 "--lat", "39.9", "--lon", "116.4", "--tz", "Asia/Shanghai")
        b = jrun("astro.py", "--date", "1993-04-12", "--time", "07:35", "--natal",
                 "--lat", "39.9", "--lon", "116.4", "--tz", "8")
        self.assertEqual(a["ascendant"]["sign"], b["ascendant"]["sign"])

    def test_historical_dst_is_resolved_not_guessed(self):
        # A July 1993 Amsterdam birth is UTC+2 (summer time), not the +1 standard offset.
        r = jrun("astro.py", "--date", "1993-07-15", "--time", "14:00", "--natal",
                 "--lat", "52.37", "--lon", "4.90", "--tz", "Europe/Amsterdam")
        self.assertTrue(any("UTC+2" in c for c in r.get("caveats", [])), r.get("caveats"))

    def test_pre_1970_birth_is_flagged_as_unverified(self):
        r = jrun("astro.py", "--date", "1930-06-15", "--time", "10:00", "--natal",
                 "--lat", "52.37", "--lon", "4.90", "--tz", "Europe/Amsterdam")
        self.assertTrue(any("pre-1970" in c for c in r.get("caveats", [])),
                        "truncated tzdata must not be presented as exact")

    def test_bad_zone_is_rejected_clearly(self):
        code, _, err = run("astro.py", "--date", "1993-04-12", "--natal",
                           "--tz", "Mars/Olympus", expect_ok=False)
        self.assertNotEqual(code, 0)
        self.assertIn("IANA", err)

    def test_no_time_means_no_ascendant_and_a_stated_caveat(self):
        r = jrun("astro.py", "--date", "1993-04-12", "--natal",
                 "--lat", "39.9", "--lon", "116.4", "--tz", "Asia/Shanghai")
        self.assertIsNone(r.get("ascendant"))
        self.assertTrue(r.get("caveats"))


class TestSynastry(unittest.TestCase):
    """合婚 is where this tradition does the most real-world damage. The engine's job
    is to compute the traditional relations and make a verdict UNAVAILABLE."""

    def chart(self, **kw):
        args = ["--a", "1993-04-12", "--a-time", "07:35", "--a-gender", "m",
                "--b", "1995-08-30", "--b-time", "14:20", "--b-gender", "f"]
        return jrun("synastry.py", *args)

    def test_branch_relations_are_computed_and_symmetric(self):
        import synastry
        # every table entry must read the same both ways round
        for z1 in synastry.ZHI:
            for z2 in synastry.ZHI:
                a = {r["relation"] for r in synastry._relations_between(z1, z2)}
                b = {r["relation"] for r in synastry._relations_between(z2, z1)}
                self.assertEqual(a, b, f"{z1}/{z2} asymmetric")

    def test_known_relations(self):
        import synastry as sy
        rel = lambda a, b: {r["relation"] for r in sy._relations_between(a, b)}
        self.assertIn("六合", rel("子", "丑"))
        self.assertIn("六冲", rel("子", "午"))
        self.assertIn("六害", rel("子", "未"))
        self.assertIn("半合", rel("申", "子"))
        self.assertIn("三会", rel("寅", "卯"))
        self.assertIn("相刑", rel("子", "卯"))
        self.assertIn("自刑", rel("辰", "辰"))
        # 寅亥 carries TWO relations at once — report both, not the flattering one
        self.assertEqual({"六合", "六破"}, rel("寅", "亥"))
        self.assertEqual(set(), rel("子", "寅"))

    def test_computed_section_carries_no_verdict(self):
        # Scan only `computed` — `refusals`/`disclaimer` necessarily QUOTE the verdict
        # in order to forbid it, exactly like a good reply does.
        r = self.chart()
        blob = json.dumps(r["computed"], ensure_ascii=False)
        for forbidden in ["compatib", "合不合", "般配", "score", "得分", "评分", "%",
                          "克夫", "克妻", "天生一对"]:
            self.assertNotIn(forbidden, blob, f"computed leaks a verdict: {forbidden}")
        # and no numeric field that could be printed as a compatibility number
        def numbers(o, path=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from numbers(v, f"{path}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    yield from numbers(v, f"{path}[{i}]")
            elif isinstance(o, (int, float)) and not isinstance(o, bool):
                yield path, o
        for path, val in numbers(r["computed"]):
            self.assertIn("tally", path,
                          f"unexpected number in the payload ({path}={val}) — the only "
                          f"numbers here should be element counts")

    def test_refusals_are_present_and_route_elsewhere(self):
        r = self.chart()
        self.assertIn("refusals", r)
        self.assertIn("relationships.md", r["refusals"]["route"])
        self.assertIn("属相", r["refusals"]["zodiac_myth"])

    def test_unknown_time_shrinks_the_comparison_and_says_so(self):
        r = jrun("synastry.py", "--a", "1993-04-12", "--b", "1995-08-30")
        pillars = [p["pillar"] for p in r["computed"]["pillar_pairs"]]
        self.assertNotIn("hour", pillars)
        self.assertTrue(any("时刻未知" in a or "时柱不参与" in a for a in r["ambiguities"]))

    def test_gate_blocks_a_synthesized_verdict(self):
        import selfcheck
        for bad in ["你们属相不合，别勉强了。", "合婚得分 78 分。", "你们俩天生一对。",
                    "她命硬克夫。"]:
            self.assertFalse(selfcheck.check(bad, "synastry")["ok"], bad)

    def test_gate_allows_the_correct_refusal(self):
        # the sentence that DECLINES the verdict necessarily contains its words
        import selfcheck
        good = ("日支亥巳冲，传统上读作张力与推拉。这只是一种文化视角下的反思，不是预测，"
                "也不是这段关系该不该继续的依据；两个人合不合，是你们怎么相处决定的。")
        self.assertTrue(selfcheck.check(good, "synastry")["ok"],
                        selfcheck.check(good, "synastry")["findings"])


class TestZiwei(unittest.TestCase):
    """A chart engine written from tables, with no second engine to check against —
    so the tests carry more weight here than anywhere else in this skill."""

    def test_selftest_passes(self):
        code, out, err = run("ziwei.py", "--selftest")
        self.assertEqual(code, 0, out + err)
        self.assertIn("SELFTEST: OK", out)

    def test_ziwei_matches_the_published_table(self):
        # THE external check: 紫微星定位表, first five days of all five 局
        import ziwei
        table = {2: ["丑", "寅", "寅", "卯", "卯"], 3: ["辰", "丑", "寅", "巳", "寅"],
                 4: ["亥", "辰", "丑", "寅", "子"], 5: ["午", "亥", "辰", "丑", "寅"],
                 6: ["酉", "午", "亥", "辰", "丑"]}
        for ju, row in table.items():
            for day, want in enumerate(row, start=1):
                self.assertEqual(ziwei.ZHI[ziwei._ziwei_position(ju, day)], want,
                                 f"{ju}局 day {day}")

    def test_sihua_palace_agrees_with_the_chart_body(self):
        # these disagreed on the first real chart rendered: the palace lookup ran
        # clockwise while the palaces themselves run counter-clockwise
        import ziwei
        c = ziwei.compute("1993-04-12", "07:35", "m")["computed"]
        where = {s["star"]: p["palace"] for p in c["palaces"] for s in p["stars"]}
        for hua, info in c["birth_sihua"].items():
            if info["palace"]:
                self.assertEqual(info["palace"], where[info["star"]],
                                 f"{hua}{info['star']} palace mismatch")

    def test_no_birth_time_yields_NO_chart_not_an_empty_one(self):
        # an empty chart with a real-looking 命宫 reads as computed; that is worse
        import ziwei
        c = ziwei.compute("1993-04-12", None, "m")["computed"]
        self.assertIsNone(c["ming_gong"])
        self.assertIsNone(c["shen_gong"])
        self.assertIsNone(c["lunar"]["hour_zhi"])
        self.assertIsNone(c["wuxing_ju"]["name"])
        self.assertEqual(c["palaces"], [])

    def test_declares_it_has_no_independent_cross_check(self):
        import ziwei
        r = ziwei.compute("1993-04-12", "07:35", "m")
        self.assertFalse(r["verification"]["independent_engine_cross_check"])
        self.assertTrue(r["not_computed"])

    def test_leap_month_is_flagged_not_silently_resolved(self):
        import ziwei
        # 2020 had a leap 4th month; find a date inside it
        found = False
        for day in range(21, 32):
            r = ziwei.compute(f"2020-05-{day:02d}", "07:35", "m")
            if r["computed"]["lunar"]["is_leap_month"]:
                found = True
                self.assertTrue(any("闰" in a for a in r["ambiguities"]))
                break
        self.assertTrue(found, "expected a leap-month date in 2020-05")


class TestAstroDailyTimezone(unittest.TestCase):
    """The daily card read the birth wall clock as UT while natal() subtracted the
    offset, so the same profile could be told two different Sun signs by the two
    modes. Daily also accepted --tz and silently dropped it."""

    def test_daily_and_natal_agree_on_the_sun_sign(self):
        import astro, datetime
        on = datetime.date(2026, 8, 23)
        for d, t in (("1993-04-20", "07:35"), ("1995-09-23", "20:00"),
                     ("1998-03-20", "16:00"), ("1991-01-20", "23:30")):
            off, _ = astro._resolve_tz("Asia/Shanghai", d, t)
            daily = astro.compute(d, t, on, tz="Asia/Shanghai")["sun_sign"]
            nat = astro.natal(d, t, lat=39.9, lon=116.4, tz_offset=off)
            ns = nat["sun"]["sign"] if isinstance(nat.get("sun"), dict) else nat.get("sun_sign")
            self.assertEqual(daily, ns, f"{d} {t}")

    def test_daily_actually_uses_the_tz_flag(self):
        r = jrun("astro.py", "--date", "1993-04-20", "--time", "07:35",
                 "--tz", "Asia/Shanghai", "--on-date", "2026-08-23")
        self.assertEqual(r["tz"], "Asia/Shanghai")
        self.assertIn("snapshot_at", r)

    def test_missing_tz_is_disclosed_not_hidden(self):
        r = jrun("astro.py", "--date", "1993-04-20", "--time", "07:35",
                 "--on-date", "2026-08-23")
        self.assertTrue(any("未提供出生地时区" in c for c in r["caveats"]), r["caveats"])

    def test_nonexistent_and_doubled_wall_clocks_are_flagged(self):
        import astro
        _, gap = astro._resolve_tz("Europe/Amsterdam", "2026-03-29", "02:30")
        self.assertIn("并不存在", gap)
        _, fold = astro._resolve_tz("Europe/Amsterdam", "2026-10-25", "02:30")
        self.assertIn("两次", fold)
        _, ok = astro._resolve_tz("Europe/Amsterdam", "2026-07-15", "14:00")
        self.assertNotIn("并不存在", ok)
        self.assertNotIn("两次", ok)

    def test_moon_near_a_sign_boundary_is_hedged(self):
        import astro, datetime
        hits = 0
        for i in range(40):
            d = datetime.date(2026, 8, 1) + datetime.timedelta(days=i)
            r = astro.compute("1993-04-12", "07:35", d, tz="Asia/Shanghai")
            hits += any("换座边界" in c for c in r["caveats"])
        self.assertGreater(hits, 0, "the Moon crosses a sign every ~2.5 days; "
                                    "40 days must contain a boundary day")

    def test_extreme_latitude_omits_houses_loudly(self):
        import astro
        r = astro.natal("1993-04-12", "07:35", lat=78.2, lon=15.6, tz_offset=1)
        self.assertIsNone(r.get("ascendant"))
        self.assertTrue(any("宫位" in c for c in r.get("caveats", [])))


class TestZiweiPlacements(unittest.TestCase):
    """The structural invariants in ziwei's --selftest check that the right SET of
    stars exists, not WHERE they land. A mutation sweep proved it: of nine deliberate
    rule errors (系 offsets, 四化 table, 禄存, 文昌 direction, 左辅 start, 命宫 counted
    forward instead of back) the suite caught exactly ONE. This pins placements.

    Honest about what it is: the fixture was generated BY this engine, so it catches
    regressions, not transcription errors. Correctness still rests on the published
    紫微星定位表 check plus the tables as written — there is no second ZWDS engine here."""

    def _golden(self):
        with open(os.path.join(SKILL, "tests", "fixtures", "ziwei_golden.json"),
                  encoding="utf-8") as f:
            return json.load(f)["charts"]

    def test_every_star_lands_where_it_did(self):
        import ziwei
        for row in self._golden():
            d, t, g, tz = row["birth"]
            c = ziwei.compute(d, t, g, tz=tz)["computed"]
            self.assertEqual(c["ming_gong"]["ganzhi"], row["ming"], row["birth"])
            self.assertEqual(c["shen_gong"]["palace"], row["shen"], row["birth"])
            self.assertEqual(c["wuxing_ju"]["name"], row["ju"], row["birth"])
            self.assertEqual(c["lunar"]["year_ganzhi"], row["year_gz"], row["birth"])
            got = {p["palace"]: {"branch": p["branch"], "stem": p["stem"],
                                 "stars": [s["star"] + ("[" + s["sihua"] + "]"
                                                        if "sihua" in s else "")
                                           for s in p["stars"]]}
                   for p in c["palaces"]}
            self.assertEqual(got, row["palaces"], f"{row['birth']} placements moved")

    def test_sihua_lands_where_it_did(self):
        import ziwei
        for row in self._golden():
            d, t, g, tz = row["birth"]
            c = ziwei.compute(d, t, g, tz=tz)["computed"]
            got = {k: [v["star"], v.get("palace")] for k, v in c["birth_sihua"].items()}
            self.assertEqual(got, {k: list(v) for k, v in row["sihua"].items()}, row["birth"])


class TestZiweiInputValidation(unittest.TestCase):
    def test_impossible_dates_are_refused_not_charted(self):
        # lunar-python silently rolls Feb 30 into March, so the engine used to return a
        # complete confident 命盘 for a date that cannot exist
        for bad in ("1993-02-30", "1993-04-31", "1993-02-29"):
            r = jrun("ziwei.py", "--date", bad, "--time", "07:35", "--gender", "m")
            self.assertFalse(r["ok"], bad)
            self.assertIn("not a real date", r["error"])

    def test_year_stem_uses_the_LUNAR_year_not_立春(self):
        # ZWDS places 命宫/紫微 from the lunar month+day, so the year stem must turn at
        # 春节. Using 立春 put a 癸酉 lunar chart under a 壬申 year stem — and the year
        # stem drives 四化/禄存/天魁天钺/火铃.
        import ziwei
        r = ziwei.compute("1993-01-28", "07:35", "m", tz="Asia/Shanghai")
        self.assertEqual(r["computed"]["lunar"]["year_ganzhi"], "癸酉")
        self.assertTrue(any("春节" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_missing_tz_is_disclosed(self):
        import ziwei
        r = ziwei.compute("1993-04-12", "07:35", "m")
        self.assertTrue(any("未提供出生地时区" in a for a in r["ambiguities"]))


class TestSafetyScan(unittest.TestCase):
    """A keyword backstop. It may miss; it must not cry wolf on ordinary venting."""

    def scan(self, text):
        return jrun("safety_scan.py", "--text", text)

    def test_catches_real_signals(self):
        for t in ["我不想活了", "感觉活着没什么意思，撑不下去了",
                  "I can't go on like this anymore", "he hits me when he's angry"]:
            self.assertTrue(self.scan(t)["crisis_flag"], t)

    def test_does_not_flag_ordinary_venting(self):
        for t in ["这个 bug 快把我搞死了，改了一天", "今天玩游戏死了三次，笑死",
                  "最近压力大，但还行", "老板说再这样下去就完蛋了",
                  "累死了，明天还要开会"]:
            self.assertFalse(self.scan(t)["crisis_flag"], t)


class TestSelfcheck(unittest.TestCase):
    """The output gate. False negatives are bad; false positives get it ignored."""

    def check(self, text, module="none"):
        import selfcheck
        return selfcheck.check(text, module)

    def test_passes_an_honest_destiny_reading(self):
        good = ("命盘是按传统规则算出来的；怎么解读是一种文化视角、帮你自我反思的镜子，"
                "不是科学预测。你盘里正官(责任、规矩、把自己嵌进体系做好)藏得厚，"
                "食神(把心里的东西做出来)也在。按「扶抑」一派估，接近中和——只是一种流派的启发。"
                "传统上倾向于把这十年读成「往深里推进」的季节。")
        r = self.check(good, "destiny")
        self.assertTrue(r["ok"], r["findings"])
        self.assertEqual(r["warnings"], 0, r["findings"])

    def test_blocks_fatalism(self):
        r = self.check("你今年一定会离婚，命中注定的，逃不掉。", "destiny")
        self.assertFalse(r["ok"])
        self.assertTrue(any(x["code"] == "fatalism" for x in r["findings"]))

    def test_blocks_fabricated_precision(self):
        for bad in ["今日综合运 ⭐⭐⭐⭐☆", "幸运数字：7", "匹配度 87%",
                    "研究表明这类人更容易成功", "年薪大概 8 万欧"]:
            r = self.check(bad, "daily")
            self.assertFalse(r["ok"], bad)

    def test_blocks_an_invented_helpline(self):
        r = self.check("可以打心理热线 400-800-1234 试试。", "crisis")
        self.assertTrue(any(x["code"] == "unknown-helpline" for x in r["findings"]))

    def test_a_birth_datetime_is_not_a_phone_number(self):
        # Found in real use: every destiny reading states the birth moment, and
        # "1993-04-12 07:35" is a hyphenated 12-digit string. Left unfixed, the most
        # important check in this gate would have cried wolf on every single chart.
        for t in ["起盘设定：1993-04-12 07:35 · 北京", "生于 1993-04-12 07:35",
                  "2026-08-22T14:30 的流日", "时效: as of 2026-08"]:
            r = self.check(t, "destiny")
            self.assertFalse(any(x["code"] == "unknown-helpline" for x in r["findings"]), t)

    def test_still_catches_an_invented_helpline_near_dates(self):
        r = self.check("生于 1993-04-12。可以打热线 400-800-1234 试试。", "crisis")
        self.assertTrue(any(x["code"] == "unknown-helpline" for x in r["findings"]))

    def test_accepts_the_canonical_helplines(self):
        for good in ["全国心理援助热线 12356 是 24 小时的",
                     "113 Zelfmoordpreventie — 0800-0113",
                     "Samaritans 116 123", "988 (call or text)"]:
            r = self.check(good, "none")
            self.assertFalse(any(x["code"] == "unknown-helpline" for x in r["findings"]), good)

    def test_crisis_reply_must_drop_the_persona_and_give_a_resource(self):
        r = self.check("你的八字今年是个坎，熬过去就好了。", "crisis")
        codes = {x["code"] for x in r["findings"]}
        self.assertIn("crisis-persona", codes)
        self.assertIn("crisis-no-resource", codes)

    def test_high_stakes_facts_require_the_factcheck_block(self):
        bad = "你毕业后可以申请 30% ruling，签证门槛是每月 3500 欧，你完全符合。"
        self.assertFalse(self.check(bad, "career")["ok"])
        good = bad + ("\n── 事实核查 ──\n· 30% ruling 的境外招聘条件\n"
                      "  来源: belastingdienst.nl · 时效: as of 2026-08\n  状态: ✅已核\n"
                      "你需要自己确认: 你首次来荷时的居留类型")
        self.assertTrue(self.check(good, "career")["ok"])

    def test_software_licences_are_not_a_licensing_question(self):
        # "licence" the legal permission to practise vs "LICENSE" the file. The bare
        # pattern matched both, so any draft mentioning the repo's own licence was
        # blocked for missing a fact-check block.
        for t in ["Code and prose: see LICENSE. Data is CC BY 4.0.",
                  "本项目采用 MIT license 发布。",
                  "The occupation data ships under a CC BY 4.0 licence."]:
            r = self.check(t, "none")
            self.assertFalse(any(x["code"] == "missing-factcheck" for x in r["findings"]), t)

    def test_real_licensing_eligibility_still_needs_the_block(self):
        for t in ["你在荷兰要先满足 professional licence requirements 才能执业。",
                  "他的学历认证还没办，能不能在这边执业不好说。",
                  "You'll need to check the licensing board exam schedule."]:
            r = self.check(t, "career")
            self.assertTrue(any(x["code"] == "missing-factcheck" for x in r["findings"]), t)

    def test_warns_on_unglossed_ten_gods(self):
        r = self.check("你的七杀很旺，所以压力大。", "destiny")
        self.assertTrue(any(x["code"] == "unglossed-jargon" for x in r["findings"]))

    def test_warns_when_strength_is_stated_as_fact(self):
        r = self.check("你身弱，用神是金水。", "destiny")
        self.assertTrue(any(x["code"] == "unhedged-strength" for x in r["findings"]))

    # --- the shapes a COMPETENT model actually produces. The blatant ones (命中注定,
    # ⭐⭐⭐⭐) are easy; these are what a first version of this gate missed 9 times out
    # of 10, and they are the ones that would reach a real person. ---

    def test_blocks_hedged_prediction(self):
        for bad in ["你这步大运走完，婚姻大概率是保不住的。",
                    "这一步十有八九要破财。",
                    "明年是你的本命年，容易出事。",
                    "这段感情多半会分手。"]:
            self.assertFalse(self.check(bad, "destiny")["ok"], bad)

    def test_blocks_forecasts_about_relatives(self):
        for bad in ["从盘上看，你母亲身体会比较弱。",
                    "你父亲的健康多半要注意，容易生病。"]:
            r = self.check(bad, "destiny")
            self.assertTrue(any(x["code"] == "kin-prediction" for x in r["findings"]), bad)

    def test_allows_kin_read_as_the_persons_own_relational_tendency(self):
        good = ("👪 家庭/六亲：比劫(同类、手足、同侪)有力——同辈、朋友、合伙人在你的故事里"
                "戏份重，是助力也需要边界。这是一种读法。")
        self.assertTrue(self.check(good, "destiny")["ok"], self.check(good, "destiny")["findings"])

    def test_allows_health_framed_as_tendency_plus_doctor(self):
        good = ("🩺 健康(只谈倾向，不诊断)：你水最旺，最该照看「脑子停不下来」——熬夜、"
                "反刍容易消耗你。真有担心请找医生。这是一种读法，不是预测。")
        self.assertTrue(self.check(good, "destiny")["ok"], self.check(good, "destiny")["findings"])

    def test_blocks_almanac_style_prohibitions(self):
        self.assertFalse(self.check("今天诸事不宜，建议闭门不出。", "daily")["ok"])

    def test_allows_agency_framed_yi_ji(self):
        good = ("✅ 宜：把上周搁下的那件事推一小步。⛔ 忌：大事先别急着拍板。"
                "水逆传统上提醒沟通慢一点——不是预测，是一种反思视角。")
        self.assertTrue(self.check(good, "daily")["ok"], self.check(good, "daily")["findings"])

    def test_blocks_astrology_stated_as_a_cause(self):
        self.assertFalse(self.check("水逆会导致你这周沟通全面出问题。", "daily")["ok"])

    def test_blocks_hiring_predictions(self):
        for bad in ["以你的背景，进大厂基本没戏。", "You definitely won't get the job."]:
            self.assertFalse(self.check(bad, "career")["ok"], bad)

    def test_blocks_precision_faked_in_words(self):
        self.assertFalse(self.check("这个岗位跟你的契合度大概是八成左右。", "career")["ok"])

    def test_blocks_labelling_and_deciding_for_them(self):
        r = self.check("她这种行为就是典型的煤气灯操控，你该离开她。", "relationships")
        self.assertTrue(any(x["code"] == "one-sided-verdict" for x in r["findings"]))

    # --- safety.md rule 7, made checkable: a reading must never settle a real,
    # high-stakes decision. The first version of this rule was BROKEN in a way that
    # looked like it worked — a bare alternation pasted into a longer pattern makes the
    # whole pattern an alternation, so it fired on any chart word anywhere. ---

    def test_blocks_a_reading_used_to_decide(self):
        for bad in ["你今年流年正财旺，所以该辞职去创业。",
                    "大运走到这一步，建议你就把房买了。",
                    "从八字看，这一步适合今年结婚。",
                    "命盘说明你该跳槽，不妨大胆一点。",
                    "要不要分手，按你俩的八字看是该分了。",
                    "盘里说时机到了，那就把合同签了。"]:
            r = self.check(bad, "destiny")
            self.assertTrue(any(x["code"] == "reading-as-decision" for x in r["findings"]), bad)

    def test_allows_declining_to_decide(self):
        # the refusal necessarily names both the chart and the decision
        for good in ["流年正财被点亮，是季节感——但这不是「你该辞职」的依据；"
                     "换不换工作要看真实的岗位。",
                     "盘不能替你决定要不要分手；那要看你们之间实际发生了什么，"
                     "我们回到关系模块。",
                     "买房这种事得看你的现金流和这套房本身；命盘说了不算。"]:
            self.assertTrue(self.check(good, "destiny")["ok"],
                            self.check(good, "destiny")["findings"])

    def test_allows_two_lenses_rhyming(self):
        good = ("你盘里正官(责任、规矩)藏得厚，跟兴趣量表上 Investigative 最高是"
                "同一个人的两种说法——是呼应，不是佐证。")
        self.assertTrue(self.check(good, "destiny")["ok"], self.check(good, "destiny")["findings"])

    def test_rule7_patterns_actually_compose(self):
        # guards the specific bug above: each component must be a grouped unit, so a
        # lone chart word can never satisfy the whole rule
        import selfcheck, re
        for pat, _ in selfcheck.RULE7:
            self.assertFalse(re.search(pat, "今年流年不错。"), f"lone chart word matched {pat[:40]}")
            self.assertFalse(re.search(pat, "他打算换工作。"), f"lone decision word matched {pat[:40]}")
            self.assertFalse(re.search(pat, "建议你多休息。"), f"lone decider matched {pat[:40]}")

    def test_english_output_is_checked_too(self):
        r = self.check("You will definitely get the job — you are destined to succeed.",
                       "career")
        self.assertFalse(r["ok"])


class TestNoRealUserDataInRepo(unittest.TestCase):
    """This repo is public. Birth data is the exact category the skill treats as
    sensitive, consent-gated and 'never leaves your machine' — so it must never end up
    in a docstring, a comment or a fixture. It did: a real birth date and city got used
    as the handy example while fixing an unrelated bug, and again in the very first
    commit. Examples come from the synthetic persona instead."""

    # the only birth data allowed in the tree, matching profile-schema.md's example
    SYNTHETIC = {"1993-04-12", "1995-08-30", "1930-06-15", "1993-07-15", "2020-05",
                 "1993-02-04", "1993-02-03", "2030-01-01", "1900-01-15",
                 "1990-01-01",   # round placeholder used by the consent tests
                 # Sun-sign boundary fixtures: dates chosen because the Sun changes
                 # sign around them, which is where a timezone error becomes visible.
                 "1993-04-20", "1995-09-23", "1998-03-20", "1991-01-20",
                 # sits between 春节 and 立春 in 1993 — the window where the lunar year
                 # and the 立春 year disagree
                 "1993-01-28", "2020-05-25"}

    def test_no_birth_dates_outside_the_synthetic_set(self):
        import re
        offenders = []
        for dp, dn, fn in os.walk(SKILL):
            # tests/ is scanned too: it is exactly where a real birth date keeps
            # getting reached for as the handy example, and excluding it made this
            # gate blind to its own most likely failure.
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for f in fn:
                if not f.endswith((".py", ".md", ".json")):
                    continue
                if f == "ziwei_golden.json":
                    # a generated placement fixture; its dates are constructed to cover
                    # all ten year stems, and its provenance is stated in the file
                    continue
                path = os.path.join(dp, f)
                text = open(path, encoding="utf-8", errors="ignore").read()
                for m in re.finditer(r"\b(19[0-9]{2}|200[0-9])-\d{2}-\d{2}\b", text):
                    tok = m.group(0)
                    if tok in self.SYNTHETIC:
                        continue
                    try:                       # an impossible date is nobody's birthday
                        _y, _m, _d = (int(x) for x in tok.split("-"))
                        __import__("datetime").date(_y, _m, _d)
                    except ValueError:
                        continue
                    offenders.append((os.path.relpath(path, SKILL), tok))
        self.assertEqual(offenders, [], f"real-looking birth dates in the repo: {offenders}")


class TestForgetAllCannotWipeAnUnrelatedDirectory(unittest.TestCase):
    """`--yes` was the ONLY guard on a recursive rmtree of whatever COMPANION_HOME
    pointed at. A typo, a stale export, or a shell variable meant for something else
    took an unrelated directory with it — verified by deleting one in testing."""

    def test_refuses_a_directory_it_did_not_create(self):
        with tempfile.TemporaryDirectory() as t:
            victim = os.path.join(t, "precious")
            os.makedirs(victim)
            keep = os.path.join(victim, "important.txt")
            with open(keep, "w") as f:
                f.write("not the skill's data")
            r = jrun("companion.py", "forget", "--all", "--yes", home=victim)
            self.assertFalse(r["ok"])
            self.assertIn("does not look like a companion home", r["error"])
            self.assertTrue(os.path.exists(keep), "an unrelated file was deleted")

    def test_refuses_a_directory_with_only_one_marker(self):
        with tempfile.TemporaryDirectory() as t:
            half = os.path.join(t, "half")
            os.makedirs(half)
            with open(os.path.join(half, "profile.yaml"), "w") as f:
                f.write("schema_version: 1\n")
            r = jrun("companion.py", "forget", "--all", "--yes", home=half)
            self.assertFalse(r["ok"])
            self.assertTrue(os.path.exists(half))

    def test_a_real_companion_home_can_still_be_wiped(self):
        with tempfile.TemporaryDirectory() as t:
            home = os.path.join(t, "real")
            run("companion.py", "init", home=home)
            r = jrun("companion.py", "forget", "--all", "--yes", home=home)
            self.assertTrue(r["ok"])
            self.assertFalse(os.path.exists(home))

    def test_still_refuses_without_yes(self):
        with tempfile.TemporaryDirectory() as t:
            home = os.path.join(t, "real")
            run("companion.py", "init", home=home)
            r = jrun("companion.py", "forget", "--all", home=home)
            self.assertFalse(r["ok"])
            self.assertTrue(os.path.exists(home))


class TestManifestsMatchReality(unittest.TestCase):
    """A marketplace description IS the informed-consent surface. An audit found the
    declared behaviour understated what runs (localhost server, first-run pip, file
    writes) and — worse — that the docs claimed 'no network' while the code pip-installs."""

    def _manifests(self):
        out = []
        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
            with open(os.path.join(SKILL, rel), encoding="utf-8") as f:
                d = json.load(f)
            out.append((rel, d["plugins"][0] if "plugins" in d else d))
        return out

    def test_capabilities_are_disclosed(self):
        for rel, d in self._manifests():
            desc = d["description"]
            for must in ("COMPANION_HOME", "127.0.0.1", "pip install",
                         "LIFE_COMPANION_NO_AUTOINSTALL"):
                self.assertIn(must, desc, f"{rel} does not disclose {must}")

    def test_no_unconditional_offline_claim(self):
        # the exact contradiction the audit named
        for rel, d in self._manifests():
            self.assertNotIn("no network calls", d["description"], rel)

    def test_versions_and_names_agree_everywhere(self):
        import yaml
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill_name = yaml.safe_load(f.read().split("---")[1])["name"]
        versions, names = set(), set()
        for rel, d in self._manifests():
            versions.add(d["version"]); names.add(d["name"])
        with open(os.path.join(SKILL, ".claude-plugin/marketplace.json"), encoding="utf-8") as f:
            versions.add(json.load(f)["metadata"]["version"])
        self.assertEqual(len(versions), 1, f"version drift: {versions}")
        self.assertEqual(names, {skill_name}, f"name drift: {names} vs {skill_name}")


class TestConsentIsEnforcedNotJustAsked(HomeCase):
    """safety.md §4, SKILL.md and both READMEs promise birth / relationships / mood are
    EACH consent-gated and that without consent data "isn't collected, inferred or
    stored". Only `mood` was ever enforced in code — the two most sensitive categories,
    one of them about a third party who never consented, were written on request."""

    def test_birth_is_refused_without_consent(self):
        r = jrun("companion.py", "set-profile", "--merge-json",
                 json.dumps({"birth": {"date": "1990-01-01"}}), home=self.home)
        self.assertFalse(r["ok"])
        self.assertIn("consent.birth", r["error"])
        with open(os.path.join(self.home, "profile.yaml"), encoding="utf-8") as f:
            self.assertNotIn("1990-01-01", f.read())

    def test_third_party_relationship_notes_refused_without_consent(self):
        r = jrun("companion.py", "cache", "--module", "relationships", "--merge-json",
                 json.dumps({"people": {"X": {"tendencies": ["anxious"]}}}), home=self.home)
        self.assertFalse(r["ok"])
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "state", "modules", "relationships.yaml")))

    def test_granting_consent_unblocks_it(self):
        run("companion.py", "consent", "--set", "birth=yes", "relationships=yes",
            home=self.home)
        self.assertTrue(jrun("companion.py", "set-profile", "--merge-json",
                             json.dumps({"birth": {"date": "1990-01-01"}}),
                             home=self.home)["ok"])
        self.assertTrue(jrun("companion.py", "cache", "--module", "relationships",
                             "--merge-json", json.dumps({"people": {"X": {}}}),
                             home=self.home)["ok"])

    def test_ungated_writes_are_unaffected(self):
        self.assertTrue(jrun("companion.py", "set-profile", "--merge-json",
                             json.dumps({"identity": {"name": "A"}}), home=self.home)["ok"])
        self.assertTrue(jrun("companion.py", "cache", "--module", "destiny",
                             "--merge-json", json.dumps({"chart": {"p": "x"}}),
                             home=self.home)["ok"])

    def test_null_only_birth_patch_is_not_treated_as_collection(self):
        # clearing fields must not require consent you are in the middle of revoking
        self.assertTrue(jrun("companion.py", "set-profile", "--merge-json",
                             json.dumps({"birth": {"date": None}}), home=self.home)["ok"])


class TestHelplineGateHoles(unittest.TestCase):
    """An invented crisis helpline is the worst output this skill can produce. The
    gate had three holes at once: a 6-digit floor (so `61120` was not even a phone
    number), a trailing '.' killing the match, and crisis-no-resource matching by
    SUBSTRING — so `61120` "contained" 112 and counted as a real resource."""

    def check(self, t, m="crisis"):
        import selfcheck
        return selfcheck.check(t, m)

    def test_short_invented_shortcode_is_caught(self):
        r = self.check("撑不住可以打 61120。")
        codes = {x["code"] for x in r["findings"]}
        self.assertIn("unknown-helpline", codes)
        self.assertIn("crisis-no-resource", codes)

    def test_invented_numbers_in_several_shapes(self):
        for t in ["可以打心理热线 400-800-1234 试试。", "Call the crisis line at 555-0142.",
                  "热线是 021.6279.8888", "Try 116 124 for support.", "打 4001619996 也行"]:
            self.assertFalse(self.check(t)["ok"], t)

    def test_real_helplines_still_pass(self):
        for t in ["全国心理援助热线 12356 是 24 小时的", "113 Zelfmoordpreventie — 0800-0113",
                  "988 (call or text)", "Samaritans 116 123",
                  "不确定你在哪的话，findahelpline.com 可以按国家找"]:
            r = self.check(t)
            self.assertFalse(any(x["code"] == "unknown-helpline" for x in r["findings"]), t)

    def test_ordinary_numbers_are_not_helplines(self):
        for t in ["起盘设定：1993-04-12 07:35", "时效: as of 2026-08", "orb 0.3°，容差很紧",
                  "第 21 题，188 个职业，其中 68 个带数值分"]:
            r = self.check(t, "destiny")
            self.assertFalse(any(x["code"] == "unknown-helpline" for x in r["findings"]), t)


class TestGateHolesFoundByAudit(unittest.TestCase):
    def check(self, t, m):
        import selfcheck
        return selfcheck.check(t, m)

    def test_hedged_fatalism_paraphrases(self):
        for t in ["这段关系很可能走到尽头。", "这一步免不了要破财。", "多半是守不住的。",
                  "这事凶多吉少。"]:
            self.assertFalse(self.check(t, "destiny")["ok"], t)

    def test_english_kin_forecasts(self):
        for t in ["Your mother's health is likely to get worse.",
                  "Your father will probably face illness this year."]:
            r = self.check(t, "destiny")
            self.assertTrue(any(x["code"] == "kin-prediction" for x in r["findings"]), t)

    def test_clinical_labels_without_the_copula(self):
        for t in ["她妥妥的回避型人格。", "典型的自恋，别理她。"]:
            self.assertFalse(self.check(t, "relationships")["ok"], t)

    def test_english_synastry_verdict_survives_the_disclaimer(self):
        # the refusal-aware matcher had no ASCII '.' in its sentence boundaries, so the
        # mandated English disclaimer disabled the whole 合婚 gate for English replies
        for t in ["You two aren't compatible. These branch relations are a cultural lens, "
                  "not a prediction.",
                  "You're not right for each other.",
                  "The charts say you shouldn't be together."]:
            self.assertFalse(self.check(t, "synastry")["ok"], t)

    def test_correct_english_refusal_still_passes(self):
        t = ("These branch relations are a cultural lens, not a prediction. Whether you "
             "two do well together isn't something the chart knows. It's made of what you do.")
        self.assertTrue(self.check(t, "synastry")["ok"], self.check(t, "synastry")["findings"])


class TestVoice(unittest.TestCase):
    """AI味 is measurable, and it is about WORDING — emoji and headers are fine.
    The skill's own output specs used to mandate the form-filling voice, so prose
    advice alone was never going to fix it."""

    HUMAN_ZH = ("🌤 今天有点拧巴。\n\n天干那头是压力，地支那头反倒跟你时柱的申凑成了"
                "半个水局。水是你缺的，所以补给这条线今天开着。\n\n收尾比开新战线划算。"
                "想拍板的事，明天再看一眼。今天流日冲你日支，传统上说那是贴身的一格被"
                "摇动，判断会比自己以为的毛躁。\n\n就这样。扶抑一派的看法，别的流派未必"
                "这么读。")
    AI_ZH = ("**🌤 今日基调**：今天不是单纯的重日：压力那面在天干，补给那面在地支。\n"
             "**🔮 分层面**：事业推着走的感觉重，适合硬啃细活，不适合开新战线。\n"
             "**💬 结合近况**：这其实正是那条线的样子——往一门东西里深钻、并且让它被检验，"
             "而不是再多扛一条战线。某种意义上，这跟昨天那句话是同一个答案的两面。\n"
             "换句话说，今天真正需要做出决定的，其实不是方向，而是节奏。")
    HUMAN_EN = ("🌤 Today pulls two ways at once.\n\nThe pressure sits on the stem. But "
                "the branch pairs with your hour pillar into a water combination, and "
                "water is what your chart runs thin on. So there's a supply line open "
                "today that usually isn't there.\n\nFinish something. Don't start "
                "something. If you're about to decide anything that matters, look again "
                "tomorrow.\n\nOne school's read. Not a forecast.")
    AI_EN = ("Let's delve into today. It's worth noting that Saturn retrograde plays a "
             "crucial role, a testament to the myriad ways these energies interact. In "
             "other words, this isn't just about work; it's about the holistic tapestry "
             "of your day. That said, the key here is balance. Ultimately, this is not a "
             "prediction, but an invitation. I hope this helps!")

    def voice(self, t):
        import selfcheck
        return selfcheck.check_voice(t)

    def test_human_writing_passes_in_both_languages(self):
        for label, t in (("zh", self.HUMAN_ZH), ("en", self.HUMAN_EN)):
            r = self.voice(t)
            self.assertTrue(r["ok_voice"], f"{label}: {r['findings']}")

    def test_machine_writing_is_flagged_in_both_languages(self):
        for label, t in (("zh", self.AI_ZH), ("en", self.AI_EN)):
            r = self.voice(t)
            self.assertGreaterEqual(len(r["findings"]), 2, f"{label}: {r['findings']}")

    def test_emoji_alone_never_triggers_anything(self):
        # explicit product decision: emoji are fine, wording is the issue
        plain = "今天有点拧巴。收尾比开新战线划算。就这样。"
        emojied = "🌤✨🔮 " + plain + " 💬🀄♓🎨✅⛔🕰️💼💰"
        self.assertEqual([f["code"] for f in self.voice(plain)["findings"]],
                         [f["code"] for f in self.voice(emojied)["findings"]])
        self.assertTrue(self.voice(emojied)["ok_voice"])

    def test_language_is_detected_and_can_be_forced(self):
        import selfcheck
        self.assertTrue(selfcheck.check_voice(self.HUMAN_ZH)["cjk"])
        self.assertFalse(selfcheck.check_voice(self.HUMAN_EN)["cjk"])
        self.assertFalse(selfcheck.check_voice(self.HUMAN_ZH, locale="en")["cjk"])

    def test_english_sentences_split_on_periods(self):
        # this was broken: without '.' every English paragraph counted as ONE sentence,
        # so human writing scored as perfectly uniform and got flagged
        import selfcheck
        n = len(selfcheck._sentences("One. Two things here. Three. And a fourth one."))
        self.assertGreaterEqual(n, 4)
        # decimals must not split
        self.assertEqual(len(selfcheck._sentences("orb 0.3 degrees of tension here")), 1)

    def test_voice_never_blocks(self):
        code, out, _ = run("selfcheck.py", "--module", "daily", "--text", self.AI_ZH)
        self.assertEqual(code, 0, "voice findings must never set a failing exit code")
        self.assertIn("voice", out)

    def test_no_voice_flag_suppresses_the_section(self):
        _, out, _ = run("selfcheck.py", "--module", "daily", "--no-voice",
                        "--text", self.AI_ZH)
        self.assertNotIn("voice [", out)


class TestDocsTeachGoodShapes(unittest.TestCase):
    """The worked examples in the module docs are what the model imitates. If an
    example would fail the gate, the skill is teaching the shape it forbids.

    Only the QUOTED examples are scanned, never a whole reference file: those files
    also quote the forbidden shapes in order to forbid them ("no invented 综合运 ⭐⭐⭐⭐"),
    and the gate cannot tell a counter-example from an example."""

    def _quoted_example(self, path, start_marker, end_marker):
        s = open(os.path.join(SKILL, path), encoding="utf-8").read()
        i = s.find(start_marker)
        self.assertGreater(i, -1, f"marker not found in {path}: {start_marker}")
        block = s[i:s.find(end_marker, i)]
        return "\n".join(l.lstrip("> ") for l in block.splitlines() if l.startswith(">"))

    def test_english_destiny_example_passes_the_gate(self):
        import selfcheck
        ex = self._quoted_example("references/modules/destiny.md",
                                  "### The same layers when `locale` is `en`",
                                  "Note what does *not* change")
        self.assertIn("正官", ex, "the English example must keep the 汉字 terms")
        self.assertIn("(", ex, "…each glossed on first use")
        r = selfcheck.check(ex, "destiny")
        self.assertTrue(r["ok"], r["findings"])
        self.assertEqual(r["warnings"], 0, r["findings"])

    def test_chinese_destiny_example_passes_the_gate(self):
        import selfcheck
        ex = self._quoted_example("references/modules/destiny.md",
                                  "**🪞 L0 · 一句话画像**",
                                  "### The same layers when `locale` is `en`")
        r = selfcheck.check(ex, "destiny")
        self.assertTrue(r["ok"], r["findings"])
        # the disclaimer lives above this block in the real output, so allow only that
        self.assertEqual([x["code"] for x in r["findings"]], ["missing-disclaimer"],
                         r["findings"])

    def test_disclaimers_cover_both_locales(self):
        s = open(os.path.join(SKILL, "assets", "disclaimers.md"), encoding="utf-8").read()
        for section in ["Destiny", "Career fit", "Relationship reflection"]:
            i = s.find(section)
            self.assertGreater(i, -1, section)
            block = s[i:i + 700]
            self.assertIn("**zh:**", block, section)
            self.assertIn("**en:**", block, section)


class TestCareerMatch(unittest.TestCase):
    def test_selftest_passes(self):
        code, out, err = run("career_match.py", "--selftest")
        self.assertEqual(code, 0, out + err)
        self.assertIn("SELFTEST: OK", out)

    def test_title_lookup_maps_everyday_words_to_soc_codes(self):
        import career_match as cm
        occs, _ = cm.load_occupations()
        for query, expect in [("核磁共振技师", "Magnetic Resonance Imaging Technologists"),
                              ("数据科学家", "Data Scientists"),
                              ("心理咨询", "Clinical and Counseling Psychologists"),
                              ("Statisticians", "Statisticians")]:
            titles = [h["title"] for h in cm.find_occupations(query, occs)]
            self.assertIn(expect, titles, f"{query} -> {titles[:4]}")

    def test_title_lookup_refuses_rather_than_guessing(self):
        # The dangerous failure is a confident wrong mapping, not an empty result.
        import career_match as cm
        occs, _ = cm.load_occupations()
        self.assertEqual(cm.find_occupations("屠龙勇士", occs), [])
        code, out, _ = run("career_match.py", "--find", "屠龙勇士")
        self.assertIn("NO MATCH", out)
        self.assertIn("Do NOT substitute", out)

    def test_title_lookup_flags_data_quality_per_hit(self):
        import career_match as cm
        occs, _ = cm.load_occupations()
        hits = cm.find_occupations("Statisticians", occs)
        self.assertTrue(hits[0]["has_numeric_interests"])
        self.assertIn("soc_code", hits[0])

    def test_onet_attribution_survives(self):
        with open(os.path.join(SKILL, "data", "career", "occupations.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        blob = json.dumps(data).lower()
        self.assertIn("o*net", blob)


class TestCareerValidity(unittest.TestCase):
    """Three measurement defects, all of which produced a confident-looking result
    that carried no information — the failure mode this skill exists to avoid."""

    def setUp(self):
        import career_match as cm
        self.cm = cm
        self.key = cm.load_scoring_key()
        self.occ, _ = cm.load_occupations()

    # --- A: an undiscriminating answer set is a non-answer -------------------
    def test_straight_lining_is_refused_not_ranked(self):
        # cosine ignores magnitude, so [k,k,k,k,k,k] is the SAME direction for every k
        for v in (0, 1, 2, 3, 4):
            r = self.cm.score_person_grouped({i: v for i in range(1, 22)},
                                             self.key, self.occ)
            self.assertTrue(r.get("refused"), f"all-{v} should be refused")
            self.assertIn("区分度", r["reason"])

    def test_a_shaped_answer_set_still_scores(self):
        resp = {i: 1 for i in range(1, 22)}
        for i in (5, 6, 7, 8):
            resp[i] = 4
        r = self.cm.score_person_grouped(resp, self.key, self.occ)
        self.assertFalse(r.get("refused"))
        self.assertEqual(len(r["numeric_interests"]) + len(r["code_only"]), len(self.occ))

    # --- B: the values cosine had a hard floor ------------------------------
    def test_opposite_value_rankings_read_low(self):
        V = list(self.cm.WORK_VALUES)
        self.assertEqual(self.cm.band(self.cm.values_fit(list(reversed(V)), V)), "Low")
        self.assertEqual(self.cm.band(self.cm.values_fit(V, V)), "Strong")

    def test_values_fit_spans_the_whole_band_range(self):
        import itertools
        V = list(self.cm.WORK_VALUES)
        vals = [self.cm.values_fit(list(p), V) for p in itertools.permutations(V)]
        self.assertLess(min(vals), 0.05)
        self.assertAlmostEqual(max(vals), 1.0, places=6)

    def test_the_declared_cosine_floor_is_still_true(self):
        # VALUES_COS_FLOOR is what the rescale subtracts; if the vector definition ever
        # changes, this catches it instead of silently skewing every values score.
        import itertools, math
        V = list(self.cm.WORK_VALUES)
        base = self.cm.values_preference_vector(V)
        raw = []
        for p in itertools.permutations(V):
            a = self.cm.values_preference_vector(list(p))
            n = sum(x * y for x, y in zip(a, base))
            da = math.sqrt(sum(x * x for x in a)); db = math.sqrt(sum(x * x for x in base))
            raw.append(n / (da * db))
        self.assertAlmostEqual(min(raw), self.cm.VALUES_COS_FLOOR, places=3)

    # --- C: two incomparable scales shared one threshold --------------------
    def test_groups_are_ranked_separately_and_labelled(self):
        resp = {i: 1 for i in range(1, 22)}
        for i in (5, 6, 7, 8):
            resp[i] = 4
        r = self.cm.score_person_grouped(resp, self.key, self.occ)
        self.assertTrue(all(p["data_quality"] == "numeric-interests"
                            for p in r["numeric_interests"]))
        self.assertTrue(all(p["data_quality"] == "code-only" for p in r["code_only"]))
        self.assertIn("不可互相", r["_note"])

    def test_every_occupation_lands_in_exactly_one_group(self):
        resp = {i: 1 for i in range(1, 22)}
        for i in (5, 6, 7, 8):
            resp[i] = 4
        r = self.cm.score_person_grouped(resp, self.key, self.occ)
        names = ([p["occupation"] for p in r["numeric_interests"]]
                 + [p["occupation"] for p in r["code_only"]])
        self.assertEqual(len(names), len(set(names)), "an occupation appears twice")
        self.assertEqual(len(names), len(self.occ))


class TestDeps(unittest.TestCase):
    def test_doctor_reports_without_installing(self):
        rep = jrun("companion.py", "doctor")
        self.assertIn("dependencies", rep)
        self.assertTrue(all("fix" in d for d in rep["dependencies"]))

    def test_missing_optional_dep_degrades_instead_of_crashing(self):
        import _deps
        self.assertIsNone(_deps.ensure("definitely-not-a-real-package",
                                       "definitely_not_a_real_package",
                                       feature="nothing", optional=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
