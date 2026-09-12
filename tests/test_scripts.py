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


class TestLunarBirthdays(HomeCase):
    """Many people — and most of their parents — know a birthday only by the lunar
    calendar. Every engine takes a solar date and nothing converted, so the model had to
    convert by hand, which this skill forbids for good reason: leap months and 29-day
    months are exactly where a hand conversion goes wrong, and lunar-python rolls nothing
    over silently only if someone asks it."""

    def convert(self, *args):
        code, out, err = run("companion.py", "lunar-to-solar", *args, home=self.home)
        return code, out

    def test_a_lunar_date_converts(self):
        code, out = self.convert("1993", "3", "21")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["solar"], "1993-04-12")

    def test_a_leap_month_is_its_own_month(self):
        leap = json.loads(self.convert("2020", "4", "10", "--leap")[1])
        plain = json.loads(self.convert("2020", "4", "10")[1])
        self.assertEqual((leap["solar"], plain["solar"]), ("2020-06-01", "2020-05-02"))

    def test_dates_that_do_not_exist_are_refused(self):
        for args, why in ((("2021", "4", "1", "--leap"), "没有闰"),
                          (("2020", "1", "30"), "只有 29 天"),
                          (("2020", "13", "1"), "1–12")):
            code, out = self.convert(*args)
            self.assertEqual(code, 2, (args, out))
            payload = json.loads(out)
            self.assertFalse(payload["ok"], args)
            self.assertIn(why, payload["error"], args)

    def _onboard(self, **birth):
        import form_server
        form = {"name": ["X"], "locale": ["zh"], "region": ["cn"], "tone": ["concise"],
                "birth_consent": ["on"], "birth_time": ["07:35"],
                "birth_place": ["Beijing, CN"], "gender": ["male"]}
        form.update({k: [v] for k, v in birth.items()})
        summary, _ = form_server.write_onboarding(self.home, form)
        import yaml
        with open(os.path.join(self.home, "profile.yaml"), encoding="utf-8") as f:
            return summary, yaml.safe_load(f)["birth"]

    def test_the_onboarding_form_takes_a_lunar_birthday(self):
        summary, birth = self._onboard(birth_calendar="lunar", birth_lunar_year="1993",
                                       birth_lunar_month="3", birth_lunar_day="21")
        self.assertEqual(str(birth["date"]), "1993-04-12")
        self.assertEqual(birth["date_input"]["calendar"], "lunar")
        self.assertIs(birth["date_input"]["leap"], False)

    def test_an_impossible_lunar_birthday_is_not_stored_and_says_why(self):
        summary, birth = self._onboard(birth_calendar="lunar", birth_lunar_year="2021",
                                       birth_lunar_month="4", birth_lunar_day="1",
                                       birth_lunar_leap="on")
        self.assertIsNone(birth["date"])
        self.assertTrue(any("没有闰" in t for t in summary["todo"]), summary["todo"])


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


class TestBaZiOneChartTwoClocks(unittest.TestCase):
    """A birth outside Beijing time is read on two clocks: 年/月柱 from the 節氣 instant,
    日/時柱 from the local clock. The first timezone fix swapped only the two DISPLAYED
    pillars; everything derived from the chart kept reading the other frame. An Amsterdam
    birth was shown 癸酉 and handed a 猴 生肖, a 大运 running the wrong way from the wrong
    month, a 五行 tally of different characters, and 年/月 十神 counted from the NEXT day's
    day master. True Solar Time broke it from the other side: it shifted the instant that
    gets compared with the 節氣, so switching it on could move 立春 itself."""

    CASES = (
        dict(date="1993-02-03", time="21:00", gender="m", tz="Europe/Amsterdam"),
        dict(date="1993-02-03", time="15:00", gender="f", tz="America/New_York"),
        dict(date="1993-02-04", time="05:00", gender="m", tz="Asia/Shanghai",
             lon=87.6, true_solar_time=True),
        dict(date="1993-04-12", time="07:35", gender="m"),      # control: one clock
    )

    @staticmethod
    def _jiazi(gz):
        import bazi
        gans, zhis = list(bazi.GAN_ELEMENT), list(bazi.ZHI_MAIN_ELEMENT)
        return next(i for i in range(60) if gans[i % 10] == gz[0] and zhis[i % 12] == gz[1])

    def _problems(self, r, male):
        import bazi
        c = r["computed"]
        p = {k: v for k, v in c["pillars"].items() if v}
        dm = p["day"]["gan"]
        out = []
        for key, pil in p.items():
            if key != "day" and pil["ten_god_gan"] != bazi._ten_god(dm, pil["gan"]):
                out.append(f"{key} 十神 {pil['ten_god_gan']} != {bazi._ten_god(dm, pil['gan'])}")
            want = [bazi._ten_god(dm, h) for h in pil["hidden_gan"]]
            if pil["ten_god_hidden"] != want:
                out.append(f"{key} 藏干十神 {pil['ten_god_hidden']} != {want}")
        main = {e: 0 for e in bazi.ELEMENTS}
        hidden = dict(main)
        for pil in p.values():
            main[bazi.GAN_ELEMENT[pil["gan"]]] += 1
            main[bazi.ZHI_MAIN_ELEMENT[pil["zhi"]]] += 1
            for h in pil["hidden_gan"]:
                hidden[bazi.GAN_ELEMENT[h]] += 1
        if c["element_tally"]["main_only"] != main:
            out.append(f"五行 {c['element_tally']['main_only']} != displayed {main}")
        if c["element_tally"]["with_hidden"] != {e: main[e] + hidden[e] for e in main}:
            out.append("五行(含藏干) disagrees with the displayed pillars")
        lp = c["luck_pillars"]
        forward = (bazi.GAN_YINYANG[p["year"]["gan"]] == "阳") == male
        if lp["direction"].startswith("顺") != forward:
            out.append(f"大运 {lp['direction']} disagrees with year stem {p['year']['gan']}")
        first = (self._jiazi(p["month"]["ganzhi"]) + (1 if forward else -1)) % 60
        if self._jiazi(lp["pillars"][0]["ganzhi"]) != first:
            out.append(f"first 大运 {lp['pillars'][0]['ganzhi']} does not follow "
                       f"month {p['month']['ganzhi']}")
        if c["daily"]["zodiac_day"]["animal"] != bazi.ZHI_ANIMAL[p["year"]["zhi"]]:
            out.append(f"生肖 {c['daily']['zodiac_day']['animal']} != year branch "
                       f"{p['year']['zhi']}")
        return out

    def test_everything_derived_reads_the_pillars_it_displays(self):
        import bazi, datetime
        for case in self.CASES:
            r = bazi.compute(**case, on_date=datetime.date(2026, 9, 12))
            self.assertEqual(self._problems(r, case["gender"] == "m"), [], case)

    def test_true_solar_time_moves_the_hour_not_the_jieqi(self):
        # Urumqi keeps Beijing time. 05:00 is 83 minutes after 立春 1993 (03:37). TST
        # pulls the local clock back past 03:37 — but 立春 has already happened.
        import bazi
        base = dict(date="1993-02-04", time="05:00", gender="m", tz="Asia/Shanghai", lon=87.6)
        plain = bazi.compute(**base)["computed"]
        tst = bazi.compute(**base, true_solar_time=True)["computed"]
        for key in ("year", "month"):
            self.assertEqual(plain["pillars"][key]["ganzhi"], tst["pillars"][key]["ganzhi"], key)
        self.assertEqual(tst["pillars"]["year"]["ganzhi"], "癸酉")
        self.assertEqual(plain["luck_pillars"]["direction"], tst["luck_pillars"]["direction"])
        self.assertNotEqual(plain["pillars"]["hour"]["zhi"], tst["pillars"]["hour"]["zhi"],
                            "TST must still move the hour pillar")

    def test_tst_across_midnight_says_the_day_pillar_moved(self):
        # 00:30 in Urumqi is about 22:20 the previous evening on the TST clock
        import bazi
        r = bazi.compute("1993-04-12", "00:30", "m", tz="Asia/Shanghai", lon=87.6,
                         true_solar_time=True)
        self.assertTrue(any("日柱" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_zishi_boundary_is_judged_on_the_clock_actually_used(self):
        # 01:40 in Urumqi is about 23:30 on the TST clock — inside the 早/晚子时 hour
        import bazi
        r = bazi.compute("1993-04-12", "01:40", "m", tz="Asia/Shanghai", lon=87.6,
                         true_solar_time=True)
        self.assertTrue(any("子时" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_one_clock_charts_match_the_library_exactly(self):
        # where both clocks agree, nothing may move: pillars, 十神 and 命宫/身宫/胎元 must
        # equal lunar-python's own methods
        import bazi
        from lunar_python import Solar
        for y, m, d, hh in ((1993, 4, 12, 7), (1995, 8, 30, 14), (1988, 11, 3, 23),
                            (2001, 7, 21, 0), (1979, 2, 4, 12), (1966, 9, 9, 18)):
            date = f"{y}-{m:02d}-{d:02d}"
            res = bazi.compute(date, f"{hh:02d}:10", "m")["computed"]
            ours = res["pillars"]
            ec = Solar.fromYmdHms(y, m, d, hh, 10, 0).getLunar().getEightChar()
            ec.setSect(2)
            for key, which in (("year", "Year"), ("month", "Month"), ("day", "Day"),
                               ("hour", "Time")):
                self.assertEqual(ours[key]["ganzhi"], getattr(ec, f"get{which}")(), (date, key))
                self.assertEqual(ours[key]["ten_god_hidden"],
                                 list(getattr(ec, f"get{which}ShiShenZhi")()), (date, key))
                if key != "day":
                    self.assertEqual(ours[key]["ten_god_gan"],
                                     getattr(ec, f"get{which}ShiShenGan")(), (date, key))
            self.assertEqual(res["extras"], {"mingong": ec.getMingGong(),
                                             "shengong": ec.getShenGong(),
                                             "taiyuan": ec.getTaiYuan()}, date)

    def test_taiyuan_follows_the_displayed_month(self):
        # 胎元 = month stem +1, month branch +3 — so it must track the month pillar shown
        import bazi
        gans, zhis = list(bazi.GAN_ELEMENT), list(bazi.ZHI_MAIN_ELEMENT)
        for case in self.CASES:
            c = bazi.compute(**case)["computed"]
            mg, mz = c["pillars"]["month"]["gan"], c["pillars"]["month"]["zhi"]
            want = gans[(gans.index(mg) + 1) % 10] + zhis[(zhis.index(mz) + 3) % 12]
            self.assertEqual(c["extras"]["taiyuan"], want, case)

    def test_unknown_hour_gives_no_minggong_or_shengong(self):
        # both hang off the birth hour; with no hour the engine read 12:00 and printed a
        # real-looking 命宫 — the same fabrication 紫微 was already fixed for
        import bazi
        ex = bazi.compute("1993-04-12", None, "f")["computed"]["extras"]
        self.assertIsNone(ex["mingong"])
        self.assertIsNone(ex["shengong"])
        self.assertIsNotNone(ex["taiyuan"], "胎元 needs only the month pillar")


class TestSynastryUsesTheChartsBaZiComputes(unittest.TestCase):
    """合婚 compared charts built with no timezone, no True Solar Time and no 早/晚子时
    setting — while its own warning told the model to add a --tz flag this script did
    not accept. For an Amsterdam birth the 合婚 year pillar (the 属相 cell) could differ
    from the person's own 命盘, so every 冲合 reported was about a chart nobody had."""

    A = dict(date="1993-02-03", time="21:00", gender="m", tz="Europe/Amsterdam")
    B = dict(date="1995-08-30", time="14:20", gender="f", tz="America/New_York")

    def _synastry(self, *extra):
        code, out, err = run("synastry.py", "--a", self.A["date"], "--a-time", self.A["time"],
                             "--a-gender", "m", "--a-tz", self.A["tz"],
                             "--b", self.B["date"], "--b-time", self.B["time"],
                             "--b-gender", "f", "--b-tz", self.B["tz"], *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    @staticmethod
    def _gz(result):
        return {k: (v["ganzhi"] if v else None)
                for k, v in result["computed"]["pillars"].items()}

    def test_each_side_is_the_chart_bazi_computes(self):
        import bazi
        r = self._synastry()
        self.assertEqual(r["computed"]["a"]["pillars"], self._gz(bazi.compute(**self.A)))
        self.assertEqual(r["computed"]["b"]["pillars"], self._gz(bazi.compute(**self.B)))

    def test_chart_conventions_reach_both_sides(self):
        import bazi
        r = self._synastry("--a-lon", "4.9", "--b-lon", "-74.0",
                           "--true-solar-time", "--early-zishi")
        conv = dict(true_solar_time=True, late_zishi=False)
        self.assertEqual(r["computed"]["a"]["pillars"],
                         self._gz(bazi.compute(**self.A, lon=4.9, **conv)))
        self.assertEqual(r["computed"]["b"]["pillars"],
                         self._gz(bazi.compute(**self.B, lon=-74.0, **conv)))

    def test_missing_tz_warning_names_flags_this_script_has(self):
        r = jrun("synastry.py", "--a", "1993-04-12", "--a-time", "07:35",
                 "--b", "1995-08-30", "--b-time", "14:20")
        text = " ".join(r["ambiguities"])
        self.assertIn("--a-tz", text)
        self.assertIn("--b-tz", text)
        self.assertEqual(len(r["ambiguities"]), len(set(r["ambiguities"])),
                         f"duplicated warnings: {r['ambiguities']}")


class TestTraditionalChineseIsReadToo(unittest.TestCase):
    """Hong Kong, Taiwan and much of the diaspora write traditional characters. Every
    pattern in the honesty gate and the crisis scan was written in simplified, so the same
    fatalistic sentence was blocked in one script and waved through in the other, and a
    traditional-script 「I want to kill myself」 raised no crisis flag at all. Text is
    folded to simplified before any pattern sees it."""

    DISCLAIMER_T = ("命盤是按傳統規則算出來的；怎麼解讀是一種文化視角、幫你自我反思的鏡子，"
                    "不是科學預測。")

    def check(self, text, module):
        import selfcheck
        return selfcheck.check(text, module)

    @staticmethod
    def _blockers(r):
        return sorted(x["code"] for x in r["findings"] if x["severity"] == "blocker")

    def test_gate_blocks_the_same_sentence_in_either_script(self):
        pairs = [("你命中注定会离婚。", "你命中註定會離婚。", "destiny"),
                 ("这步大运你一定会破财，明年多半会生病。", "這步大運你一定會破財，明年多半會生病。",
                  "destiny"),
                 ("今日综合运势：88分，幸运数字 7。", "今日綜合運勢：88分，幸運數字 7。", "daily")]
        for simp, trad, module in pairs:
            s, t = self.check(simp, module), self.check(trad, module)
            self.assertFalse(s["ok"], simp)
            self.assertEqual(self._blockers(s), self._blockers(t), trad)

    def test_evidence_quotes_what_was_actually_written(self):
        r = self.check("你命中註定會離婚。", "destiny")
        self.assertIn("註定", " ".join(x["evidence"] for x in r["findings"]))

    def test_a_clean_traditional_reading_still_passes(self):
        r = self.check(self.DISCLAIMER_T + "你盤裡正官(責任、規矩、把自己嵌進體系做好)藏得厚。",
                       "destiny")
        self.assertTrue(r["ok"], r["findings"])
        self.assertEqual(r["warnings"], 0, r["findings"])

    def test_crisis_scan_reads_traditional(self):
        for t in ["我想自殺", "活著沒意思，撐不下去了", "我不想活了"]:
            self.assertEqual(jrun("safety_scan.py", "--text", t)["severity"], "high", t)
        self.assertIn("coercive_control",
                      jrun("safety_scan.py", "--text", "他控制我的錢和手機，不讓我見朋友")["categories"])

    def test_patterns_are_written_in_simplified(self):
        # input is folded before matching, so a traditional character inside a pattern
        # could never match anything — keep every pattern constant in simplified
        try:
            import _zh
        except ImportError:
            self.fail("scripts/_zh.py is missing")
        import re as _re
        import selfcheck, safety_scan

        def strings(o):
            if isinstance(o, str):
                yield o
            elif isinstance(o, _re.Pattern):
                yield o.pattern
            elif isinstance(o, dict):
                for k, v in o.items():
                    yield from strings(k)
                    yield from strings(v)
            elif isinstance(o, (list, tuple, set, frozenset)):
                for v in o:
                    yield from strings(v)

        for mod in (selfcheck, safety_scan):
            bad = sorted({ch for name, val in vars(mod).items() if not name.startswith("__")
                          for s in strings(val) for ch in s if _zh.to_simplified(ch) != ch})
            self.assertEqual(bad, [], mod.__name__)

    def test_folding_keeps_every_offset_in_place(self):
        try:
            import _zh
        except ImportError:
            self.fail("scripts/_zh.py is missing")
        t = "這步大運你一定會破財 — ok 123 🌤"
        self.assertEqual(len(_zh.to_simplified(t)), len(t))
        self.assertEqual(_zh.to_simplified(t), "这步大运你一定会破财 — ok 123 🌤")


class TestSafetyScanBurdenPhrasing(unittest.TestCase):
    """Believing the people around you would be better off without you is one of the
    clearest warning signs there is, and it contains none of the words the list looked for."""

    def scan(self, t):
        return jrun("safety_scan.py", "--text", t)

    def test_burden_phrasing_is_flagged(self):
        for t in ["没有我大家会过得更好", "沒有我大家會過得更好", "我就是个累赘",
                  "我不在了你们就解脱了", "I'm just a burden to everyone",
                  "everyone would be better off without me"]:
            self.assertTrue(self.scan(t)["crisis_flag"], t)

    def test_ordinary_sentences_with_the_same_words_are_not(self):
        for t in ["这个项目没有我也能做得更好", "背包太重，是个负担", "明天不在家，你们自己吃"]:
            self.assertFalse(self.scan(t)["crisis_flag"], t)


class TestBoundaryWarnings(unittest.TestCase):
    """A chart is only as exact as the birth time behind it, and the engine said so in two
    places only: the 23:00 子时 and 立春. 08:58 is 丙辰 and 09:02 is 丁巳 without a word; the
    other eleven 節 move the month pillar just as silently; "around nine" had no way to be
    said at all; and a chart that knew the longitude never mentioned that the True Solar
    Time hour — the one many apps show — is a different pillar."""

    @staticmethod
    def chart(**kw):
        import bazi
        return bazi.compute(**kw)

    @staticmethod
    def _jie(year, name):
        import datetime
        from lunar_python import Solar
        s = Solar.fromYmd(year, 6, 15).getLunar().getJieQiTable()[name]
        return datetime.datetime(s.getYear(), s.getMonth(), s.getDay(), s.getHour(), s.getMinute())

    def test_minutes_from_a_shichen_boundary_names_both_pillars(self):
        for t in ("08:58", "09:02"):
            r = self.chart(date="1993-04-12", time=t, gender="m", tz="Asia/Shanghai")
            notes = [a for a in r["ambiguities"] if "时辰" in a and "分界" in a]
            self.assertTrue(notes, (t, r["ambiguities"]))
            self.assertIn("丙辰", notes[0])
            self.assertIn("丁巳", notes[0])

    def test_half_an_hour_from_a_boundary_stays_quiet(self):
        r = self.chart(date="1993-04-12", time="09:30", gender="m", tz="Asia/Shanghai")
        self.assertFalse(any("分界" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_every_jie_flags_the_month_pillar_not_only_lichun(self):
        import datetime
        moment = self._jie(2022, "芒种") + datetime.timedelta(minutes=40)
        r = self.chart(date=moment.date().isoformat(), time=moment.strftime("%H:%M"),
                       gender="f", tz="Asia/Shanghai")
        self.assertTrue(any("芒种" in a and "月柱" in a for a in r["ambiguities"]),
                        r["ambiguities"])

    def test_hours_away_from_a_jie_says_the_month_pillar_is_safe(self):
        import datetime
        moment = self._jie(2022, "芒种") + datetime.timedelta(hours=20)
        r = self.chart(date=moment.date().isoformat(), time=moment.strftime("%H:%M"),
                       gender="f", tz="Asia/Shanghai")
        notes = [a for a in r["ambiguities"] if "芒种" in a]
        self.assertTrue(notes, r["ambiguities"])
        self.assertIn("不受影响", notes[0])

    def test_a_known_longitude_names_the_true_solar_time_hour(self):
        base = dict(date="1993-04-12", time="07:40", gender="m", tz="Asia/Shanghai", lon=87.6)
        r = self.chart(**base)
        clock = r["computed"]["pillars"]["hour"]["ganzhi"]
        solar = self.chart(**base, true_solar_time=True)["computed"]["pillars"]["hour"]["ganzhi"]
        self.assertNotEqual(clock, solar)
        notes = [a for a in r["ambiguities"] if "真太阳时" in a]
        self.assertTrue(notes, r["ambiguities"])
        self.assertIn(clock, notes[0])
        self.assertIn(solar, notes[0])

    def test_no_longitude_means_no_true_solar_time_note(self):
        r = self.chart(date="1993-04-12", time="07:40", gender="m", tz="Asia/Shanghai")
        self.assertFalse(any("真太阳时" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_an_approximate_time_lists_every_pillar_it_could_be(self):
        r = self.chart(date="1993-04-12", time="09:00", gender="m", tz="Asia/Shanghai",
                       time_window=30)
        w = r["computed"]["time_window"]
        self.assertEqual(w["minutes"], 30)
        self.assertEqual(set(w["pillars"]["hour"]), {"丙辰", "丁巳"})
        self.assertEqual(w["changes"], ["hour"])
        self.assertTrue(any("±30" in a for a in r["ambiguities"]), r["ambiguities"])

    def test_the_cli_takes_a_time_window(self):
        code, out, err = run("bazi.py", "--date", "1993-04-12", "--time", "09:00", "--gender",
                             "m", "--tz", "Asia/Shanghai", "--time-window", "60",
                             "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertIn("time_window", json.loads(out)["computed"])

    def test_ziwei_flags_a_shichen_boundary_too(self):
        import ziwei
        r = ziwei.compute("1993-04-12", "08:58", "m", tz="Asia/Shanghai")
        self.assertTrue(any("时辰" in a and "分界" in a for a in r["ambiguities"]),
                        r["ambiguities"])


class TestApproximateBirthTimeInTheForm(HomeCase):
    """「大概九点」 had nowhere to go: the form offered a time or 'unknown', so a rough time
    was stored as an exact one and every pillar computed from it looked certain."""

    def _onboard(self, **birth):
        import form_server
        import yaml
        form = {"name": ["X"], "locale": ["zh"], "region": ["cn"], "tone": ["concise"],
                "birth_consent": ["on"], "birth_date": ["1993-04-12"],
                "birth_place": ["Beijing, CN"], "gender": ["male"]}
        form.update({k: [v] for k, v in birth.items()})
        form_server.write_onboarding(self.home, form)
        with open(os.path.join(self.home, "profile.yaml"), encoding="utf-8") as f:
            return yaml.safe_load(f)["birth"]

    def test_an_approximate_time_is_stored_with_its_window(self):
        birth = self._onboard(birth_time="09:00", birth_time_accuracy="approx",
                              birth_time_window="60")
        self.assertEqual(birth["time"], "09:00")
        self.assertTrue(birth["time_known"])
        self.assertEqual(birth["time_accuracy"], "approx")
        self.assertEqual(birth["time_window_min"], 60)

    def test_choosing_unknown_drops_whatever_time_was_typed(self):
        birth = self._onboard(birth_time="09:00", birth_time_accuracy="unknown")
        self.assertIsNone(birth["time"])
        self.assertFalse(birth["time_known"])
        self.assertEqual(birth["time_accuracy"], "unknown")


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

    def test_traditional_script_resolves_like_simplified(self):
        # 港澳台 users write 臺北 and 澳門. The aliases are simplified, so both used to come
        # back "no match", and the person was told to name a bigger city.
        for q, expect in [("臺北", "Asia/Taipei"), ("臺灣", "Asia/Taipei"), ("澳門", "Asia/Macau"),
                          ("東京", "Asia/Tokyo"), ("首爾", "Asia/Seoul"),
                          ("墨爾本", "Australia/Melbourne"), ("紐西蘭", "Pacific/Auckland")]:
            self.assertIn(expect, self.resolve(q), q)

    def test_places_with_their_own_crisis_lines_resolve(self):
        # a crisis line is picked by country, and a place that doesn't resolve gets none
        for q, expect in [("吉隆坡", "Asia/Kuala_Lumpur"), ("马来西亚", "Asia/Kuala_Lumpur"),
                          ("Malaysia", "Asia/Kuala_Lumpur"), ("槟城", "Asia/Kuala_Lumpur"),
                          ("Taiwan", "Asia/Taipei"), ("高雄", "Asia/Taipei"),
                          ("New Zealand", "Pacific/Auckland"), ("惠灵顿", "Pacific/Auckland"),
                          ("澳洲", "Australia/Sydney"), ("雪梨", "Australia/Sydney"),
                          ("布里斯班", "Australia/Brisbane"), ("珀斯", "Australia/Perth"),
                          ("大阪", "Asia/Tokyo"), ("釜山", "Asia/Seoul")]:
            self.assertIn(expect, self.resolve(q), q)


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
                 "1993-01-28", "2020-05-25",
                 # the same synthetic birthday written on the lunar calendar: the
                 # date_input example in profile-schema.md
                 "1993-03-21"}

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
        # (the destiny cache holds the pillars — birth data in another form — so it is
        # gated by birth consent now; see TestComputeWithoutStoring)
        self.assertTrue(jrun("companion.py", "cache", "--module", "career_intake",
                             "--merge-json", json.dumps({"latest": {"answered": 0}}),
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


class TestHelplineGateReadsRealNumbersInContext(unittest.TestCase):
    """The gate blocked real lines as soon as they were written the way people write
    them. `12356 24 小时` tokenised as one number, 1235624, so the national line was
    "invented" and the reply was told to drop it; the year in `2025 起全国统一` read as a
    four-digit shortcode; a grouped figure like `12,345` left a stray `345` to flag."""

    def codes(self, t, m="crisis"):
        import selfcheck
        return {x["code"] for x in selfcheck.check(t, m)["findings"]}

    def test_a_real_number_followed_by_its_hours_passes(self):
        for t in ["撑不住的话，拨打全国心理援助热线 12356 24 小时都有人接。",
                  "Samaritans 116 123 24/7, free.",
                  "1. 12356 全国心理援助热线",
                  "全国心理援助热线 12356（2025 起全国统一，24/7）"]:
            c = self.codes(t)
            self.assertNotIn("unknown-helpline", c, t)
            self.assertNotIn("crisis-no-resource", c, t)

    def test_an_invented_number_next_to_hours_is_still_caught(self):
        for t in ["拨打心理热线 12357 24 小时都有人接。", "Try 116 124 24/7.",
                  "心理热线打 2025 就行。", "1. 12357 心理热线"]:
            self.assertIn("unknown-helpline", self.codes(t), t)

    def test_a_grouped_figure_is_not_a_phone_number(self):
        self.assertNotIn("unknown-helpline",
                         self.codes("热线每年接到 12,345 通电话，你并不孤单。请拨打 12356。"))


class TestCrisisLinesForMoreRegions(unittest.TestCase):
    """Hong Kong, Macau, Taiwan, Singapore, Malaysia, Australia, New Zealand, Japan,
    South Korea, Germany and France had no line of their own, and the gate blocks every
    number outside its list, so a correct local line was forced out of the reply. Each
    number here was read on its operator's or government's own page on 2026-09-13."""

    LINES = ["情緒通 18111，24 小時", "香港撒瑪利亞防止自殺會 2389 2222", "生命熱線 2382 0000",
             "撒瑪利亞會 2896 0000", "芷若園 18281", "向晴熱線 18288", "和諧之家 2522 0434",
             "澳門明愛生命熱線 2852 5222", "外語熱線 2852 5777", "社工局 24 小時電話熱線 28261126",
             "家庭暴力求助專線 28233030", "安心專線 1925", "生命線 1995", "113 保護專線",
             "national mindline 1771", "WhatsApp +65-6669-1771", "Samaritans of Singapore 1767",
             "CareText 9151 1767", "NAVH 1800 777 0000", "call 995",
             "Befrienders KL +603-7627 2929", "Talian HEAL 15555", "Talian Kasih 15999",
             "WhatsApp 019 26 15999", "Lifeline 13 11 14", "text 0477 13 11 14",
             "Suicide Call Back Service 1300 659 467",
             "Call 1800RESPECT on 1800 737 732, text 0458 737 732", "call 000 now",
             "call or text 1737", "Suicide Crisis Helpline 0508 828 865",
             "0508 TAUTOKO (0508 828 865)", "Are You OK 0800 456 450",
             "Call the Crisisline on 0800 REFUGE or 0800 733 843", "call 111 now",
             "#いのちSOS 0120-061-338", "よりそいホットライン 0120-279-338", "福島県から 0120-279-226",
             "DV相談＋ 0120-279-889", "DV相談ナビ #8008", "韩国自杀预防热线 109",
             "정신건강 상담전화 1577-0199", "여성긴급전화 1366", "call 119",
             "TelefonSeelsorge 0800 1110111 / 0800 1110222", "Hilfetelefon 116 016",
             "call 3114", "call 3919", "SMS 114"]

    def codes(self, t, m="crisis"):
        import selfcheck
        return {x["code"] for x in selfcheck.check(t, m)["findings"]}

    def test_each_verified_line_passes(self):
        for t in self.LINES:
            c = self.codes(t)
            self.assertNotIn("unknown-helpline", c, t)
            self.assertNotIn("crisis-no-resource", c, t)

    def test_near_misses_are_still_blocked(self):
        for t in ["情緒通熱線 18112", "Lifeline 13 11 15", "#いのちSOS 热线 0120-061-339",
                  "韩国自杀预防热线 1099", "Hilfetelefon hotline 116 017",
                  "Call 1800RESPECT on 1800 737 733"]:
            self.assertIn("unknown-helpline", self.codes(t), t)

    def test_a_year_or_a_grouped_figure_names_no_resource(self):
        # 000 and 1995 are real lines now; the 000 of `1,000` and the 1995 of `1995 年`
        # still point nobody anywhere
        for t in ["你不是一个人，我们每年陪伴超过 1,000 人。", "我 1995 年也经历过很难的时候。"]:
            self.assertIn("crisis-no-resource", self.codes(t), t)

    def test_every_number_in_the_crisis_docs_is_a_known_line(self):
        # The SKILL.md table, safety.md §2 and KNOWN_HELPLINES are three copies of one
        # list. Every number of three or more digits in the two docs must be a known line,
        # with or without helpline words around it, so a line added to one copy and not
        # the others fails here.
        import selfcheck
        skill = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()
        safety = open(os.path.join(SKILL, "references", "safety.md"), encoding="utf-8").read()
        for name, sec in [("SKILL.md", skill[skill.index("## Crisis"):skill.index("## Language")]),
                          ("safety.md §2", safety[safety.index("## 2. Crisis handling"):safety.index("## 3.")])]:
            sec = selfcheck.SERVICE_NAMES_WITH_DIGITS.sub(lambda m: " " * len(m.group(0)), sec)
            unknown = [m.group(0) for m in selfcheck.NUM_TOKEN_RE.finditer(sec)
                       if len(selfcheck._digits(m.group(0))) >= 3
                       and not (selfcheck._is_known_line(m.group(0))
                                or selfcheck._is_a_year(sec, m)
                                or selfcheck._in_a_grouped_figure(sec, m))]
            self.assertEqual(unknown, [], name)
            for region in ("Hong Kong", "Macau", "Taiwan", "Singapore", "Malaysia", "Australia",
                           "New Zealand", "Japan", "South Korea", "Germany", "France"):
                self.assertIn(region, sec, f"{name} has no {region}")


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

    def test_a_single_instance_never_fires(self):
        # short text is clamped to a quarter unit, so ONE occurrence in a short
        # paragraph scored 4.0 against a limit of 2 — the check contradicting its own
        # advice, which says a single use is a good rhetorical move
        one = "它更擅长的是拒绝。不是提醒，是拦住。"
        self.assertTrue(self.voice(one)["ok_voice"], self.voice(one)["findings"])

    def test_the_habit_is_still_caught(self):
        many = ("不是提醒，是拦住。不是猜，是算。不是预测，是镜子。"
                "不是替你决定，是给你看。这不是结论，是起点。")
        codes = [f["code"] for f in self.voice(many)["findings"]]
        self.assertIn("not-x-but-y", codes)

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


class TestFindMatchesWhatPeopleSay(unittest.TestCase):
    """`--find` ran its synonym table over the occupation TITLES as well as the query, by
    substring. 「ui」 sits inside "equipment", so 「UI设计师」 came back topped by Agricultural
    Equipment Operators. 「MRI技师」 was one unsplittable token and matched nothing, though MRI
    technologists are in the data. 「平面设计师」 tied Fashion, Graphic and Interior Designers
    and alphabetical order put Fashion first. And a single shared word — 「建筑师」 and
    Database Architects share "architect" — came back looking exactly like a real match."""

    @classmethod
    def setUpClass(cls):
        import career_match as cm
        cls.cm = cm
        cls.occs, _ = cm.load_occupations()

    def find(self, q):
        return self.cm.find_occupations(q, self.occs)

    def test_a_short_latin_alias_never_matches_inside_a_word(self):
        hits = self.find("UI设计师")
        self.assertEqual(hits[0]["title"], "Web and Digital Interface Designers",
                         [h["title"] for h in hits[:3]])
        self.assertFalse(any("Equipment" in h["title"] for h in hits),
                         [h["title"] for h in hits])

    def test_latin_written_against_chinese_is_split(self):
        for q in ("MRI技师", "MRI 技师"):
            hits = self.find(q)
            self.assertTrue(hits, q)
            self.assertEqual(hits[0]["title"], "Magnetic Resonance Imaging Technologists", q)

    def test_the_qualifier_decides_between_same_head_nouns(self):
        self.assertEqual(self.find("平面设计师")[0]["title"], "Graphic Designers")

    def test_a_single_shared_word_is_labelled_weak(self):
        # neither has a counterpart in the shipped list; one shared word is not a mapping
        for q in ("建筑师", "老师"):
            hits = self.find(q)
            self.assertTrue(hits, q)
            self.assertTrue(all(h["match"] == "weak" for h in hits), (q, hits[:2]))

    def test_an_approximate_alias_never_reads_as_strong(self):
        # O*NET has no product-manager occupation; the alias points at neighbours
        self.assertTrue(all(h["match"] == "weak" for h in self.find("产品经理")))

    def test_real_mappings_are_strong(self):
        for q, title in (("核磁共振技师", "Magnetic Resonance Imaging Technologists"),
                         ("数据科学家", "Data Scientists"), ("Statisticians", "Statisticians"),
                         ("平面设计师", "Graphic Designers"), ("MRI技师",
                          "Magnetic Resonance Imaging Technologists")):
            hit = next((h for h in self.find(q) if h["title"] == title), None)
            self.assertIsNotNone(hit, q)
            self.assertEqual(hit["match"], "strong", (q, hit))

    def test_cli_says_when_every_candidate_is_weak(self):
        code, out, _ = run("career_match.py", "--find", "建筑师", "--json")
        self.assertEqual(code, 0, out)
        self.assertIn("weak", json.loads(out)["_note"])


class TestFindMarksEquallyGoodTitles(unittest.TestCase):
    """「大学教授」 covers "Teachers, Postsecondary" in every subject equally well. Alphabetical
    order handed Engineering the top slot, labelled strong, as if it had been chosen."""

    @classmethod
    def setUpClass(cls):
        import career_match as cm
        cls.cm = cm
        cls.occs, _ = cm.load_occupations()

    def find(self, q):
        return self.cm.find_occupations(q, self.occs)

    def test_equally_good_titles_are_marked_as_a_tie(self):
        strong = [h for h in self.find("大学教授") if h["match"] == "strong"]
        self.assertGreater(len(strong), 1, strong)
        self.assertTrue(all(h.get("tied") for h in strong), strong)

    def test_a_clear_winner_is_not_a_tie(self):
        self.assertFalse(self.find("平面设计师")[0].get("tied"))

    def test_each_hit_says_which_title_words_matched(self):
        self.assertEqual(self.find("平面设计师")[0]["matched_on"], ["designers", "graphic"])

    def test_cli_says_to_ask_which_when_the_top_is_tied(self):
        code, out, _ = run("career_match.py", "--find", "大学教授", "--json")
        self.assertEqual(code, 0, out)
        self.assertIn("equally", json.loads(out)["_note"])


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


def _home_text(home):
    """Every byte of every file under a companion home, for residue searches."""
    out = []
    for dp, _, fn in os.walk(home):
        for f in fn:
            with open(os.path.join(dp, f), encoding="utf-8", errors="ignore") as fh:
                out.append(fh.read())
    return "\n".join(out)


def _index_rows(home):
    path = os.path.join(home, "journal", "index.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class TestRevokedConsentStopsUse(HomeCase):
    """Revoking consent flipped one boolean in consent.yaml and changed nothing else:
    relationship_patterns.py kept analysing the person, `brief` kept serving the birth
    block, `trend` kept averaging moods — and add-entry stored another person's name with
    no relationships consent at all. The contract now: revoking STOPS every read and write
    of that category, says what is still stored, and names the command that deletes it.
    Deleting stays its own explicit step (`forget`), so a mistaken revoke loses nothing."""

    def _seed_relationships(self):
        run("companion.py", "consent", "--set", "relationships=yes", "mood=yes", home=self.home)
        run("companion.py", "cache", "--module", "relationships", "--merge-json", json.dumps(
            {"people": {"小李": {"relationship": "partner", "incidents": [
                {"date": "2026-08-10", "gist": "吵架", "lens": "pursue-withdraw"},
                {"date": "2026-08-20", "gist": "冷战", "lens": "pursue-withdraw"}]}}}),
            home=self.home)
        for day in ("2026-08-10", "2026-08-20"):
            run("companion.py", "add-entry", "--date", day, "--text", "又和小李闹别扭",
                "--people", "小李", "--mood", "3", home=self.home)

    def test_people_are_not_stored_without_relationships_consent(self):
        r = jrun("companion.py", "add-entry", "--text", "和小李吵架了", "--people", "小李",
                 home=self.home)
        self.assertTrue(r["ok"])
        self.assertTrue(any("people" in d for d in r.get("dropped", [])), r)
        self.assertEqual(_index_rows(self.home)[0]["people"], [])

    def test_revoking_says_what_is_kept_and_how_to_delete_it(self):
        self._seed_relationships()
        r = jrun("companion.py", "consent", "--set", "relationships=no", home=self.home)
        self.assertTrue(r["ok"])
        self.assertIn("relationships", r.get("retained", {}), r)
        self.assertIn("forget --relationships", json.dumps(r, ensure_ascii=False))

    def test_revoked_relationships_are_not_read_any_more(self):
        self._seed_relationships()
        run("companion.py", "consent", "--set", "relationships=no", home=self.home)
        code, out, _ = run("relationship_patterns.py", "--format", "json", home=self.home)
        self.assertEqual(code, 3, out)
        self.assertNotIn("小李", out)
        code, out, _ = run("companion.py", "cache", "--module", "relationships", home=self.home)
        self.assertEqual(code, 3, out)
        self.assertNotIn("小李", out)
        t = jrun("companion.py", "trend", "--days", "36500", home=self.home)
        self.assertNotIn("小李", json.dumps(t, ensure_ascii=False))

    def test_revoked_birth_is_withheld_from_brief_but_not_deleted(self):
        run("companion.py", "consent", "--set", "birth=yes", home=self.home)
        run("companion.py", "set-profile", "--merge-json",
            json.dumps({"birth": {"date": "1993-04-12", "time": "07:35"}}), home=self.home)
        run("companion.py", "consent", "--set", "birth=no", home=self.home)
        b = jrun("companion.py", "brief", home=self.home)
        self.assertNotIn("1993-04-12", json.dumps(b, ensure_ascii=False))
        with open(os.path.join(self.home, "profile.yaml"), encoding="utf-8") as f:
            self.assertIn("1993-04-12", f.read(), "revoking must not delete; that is forget")
        run("companion.py", "consent", "--set", "birth=yes", home=self.home)
        self.assertEqual(jrun("companion.py", "brief", home=self.home)
                         ["profile"]["birth"]["date"], "1993-04-12")

    def test_revoked_mood_is_withheld_from_brief_and_trend(self):
        run("companion.py", "consent", "--set", "mood=yes", home=self.home)
        for m in ("3", "4", "5", "6"):
            run("companion.py", "add-entry", "--text", "x", "--mood", m, home=self.home)
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"recent_moods": [3, 4]}), home=self.home)
        run("companion.py", "consent", "--set", "mood=no", home=self.home)
        t = jrun("companion.py", "trend", "--days", "30", home=self.home)
        self.assertIsNone(t["mood_avg"])
        self.assertIsNone(t["mood_direction"])
        b = jrun("companion.py", "brief", home=self.home)
        self.assertEqual(b["continuity"]["recent_moods"], [])
        self.assertTrue(all(r["mood"] is None for r in b["journal"]["recent"]))


class TestForgetLeavesNoTrace(HomeCase):
    """「删除是真删」 was true of the one file each command named and false everywhere else.
    `forget --month` removed the journal file and left the month's quarrel in the rolling
    summary, its follow-up thread and the person's incident log; `forget --birth` left the
    birth date in the onboarding form's result marker; and the only way to delete one
    entry — including the crisis entry safety.md promises can be deleted — was to delete
    the whole month. These search the WHOLE home afterwards, because residue lands
    wherever nobody thought to look."""

    def test_forget_month_removes_that_month_from_every_store(self):
        import yaml
        run("companion.py", "consent", "--set", "relationships=yes", "mood=yes", home=self.home)
        run("companion.py", "add-entry", "--date", "2026-07-02", "--text", "七月很平静",
            "--mood", "6", home=self.home)
        run("companion.py", "add-entry", "--date", "2026-08-10", "--text",
            "AUG-SECRET 和小李吵架", "--people", "小李", "--mood", "3", home=self.home)
        run("companion.py", "cache", "--module", "relationships", "--merge-json", json.dumps(
            {"people": {
                "小李": {"incidents": [
                    {"date": "2026-07-01", "gist": "七月的小事", "lens": "criticism"},
                    {"date": "2026-08-10", "gist": "AUG-SECRET 争吵", "lens": "pursue-withdraw"}]},
                "老王": {"incidents": [
                    {"date": "2026-08-12", "gist": "AUG-SECRET 同事冲突", "lens": "criticism"}]}}}),
            home=self.home)
        run("companion.py", "continuity", "--merge-json", json.dumps(
            {"rolling_summary": "AUG-SECRET 和小李反复吵架",
             "open_threads": [
                 {"thread": "AUG-SECRET 好好谈一次", "opened": "2026-08-10", "status": "open"},
                 {"thread": "七月的计划", "opened": "2026-07-02", "status": "open"}],
             "recent_moods": [3]}), home=self.home)
        r = jrun("companion.py", "forget", "--month", "2026-08", home=self.home)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("AUG-SECRET", _home_text(self.home))
        _, out, _ = run("companion.py", "journal", home=self.home)
        self.assertIn("七月很平静", out)
        with open(os.path.join(self.home, "state", "modules", "relationships.yaml"),
                  encoding="utf-8") as f:
            people = (yaml.safe_load(f) or {})["people"]
        self.assertEqual([i["date"] for i in people["小李"]["incidents"]], ["2026-07-01"])
        self.assertNotIn("老王", people, "nothing is left of someone known only from August")
        with open(os.path.join(self.home, "state", "continuity.yaml"), encoding="utf-8") as f:
            cont = yaml.safe_load(f)
        self.assertEqual([t["thread"] for t in cont["open_threads"]], ["七月的计划"])

    def test_forget_birth_removes_it_from_the_form_marker_too(self):
        import form_server
        form_server.write_onboarding(self.home, {
            "name": ["X"], "locale": ["zh"], "region": ["cn"], "tone": ["concise"],
            "birth_consent": ["on"], "birth_date": ["1993-04-12"], "birth_time": ["07:35"],
            "birth_place": ["Beijing, CN"], "gender": ["m"]})
        self.assertIn("1993-04-12", _home_text(self.home), "setup must really store it")
        run("companion.py", "forget", "--birth", home=self.home)
        self.assertNotIn("1993-04-12", _home_text(self.home))

    def _four_entries(self):
        for day, text in (("2026-08-01", "第一条 早上"), ("2026-08-02", "第二条 MIDDLE-A"),
                          ("2026-08-02", "第三条 MIDDLE-B"), ("2026-08-03", "第四条 最后")):
            run("companion.py", "add-entry", "--date", day, "--text", text, home=self.home)

    def test_forget_one_entry_keeps_the_rest_readable(self):
        self._four_entries()
        r = jrun("companion.py", "forget", "--entry", "2026-08-02", "--nth", "2",
                 home=self.home)
        self.assertTrue(r["ok"], r)
        self.assertNotIn("MIDDLE-B", _home_text(self.home))
        _, out, _ = run("companion.py", "journal", "--limit", "10", home=self.home)
        for keep in ("第一条", "MIDDLE-A", "第四条"):
            self.assertIn(keep, out)
        rows = _index_rows(self.home)
        self.assertEqual(len(rows), 3)
        for row in rows:      # every surviving row still points at its own entry
            with open(os.path.join(self.home, row["file"]), encoding="utf-8") as f:
                self.assertTrue(f.read()[row["offset"]:].lstrip("\n")
                                .startswith(f"## {row['date']}"), row)

    def test_forget_entry_refuses_an_ambiguous_date(self):
        self._four_entries()
        r = jrun("companion.py", "forget", "--entry", "2026-08-02", home=self.home)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r.get("candidates", [])), 2, r)
        self.assertIn("MIDDLE-B", _home_text(self.home), "an ambiguous call deletes nothing")

    def test_forget_entry_refuses_when_the_index_no_longer_matches_the_file(self):
        self._four_entries()
        path = os.path.join(self.home, "journal", "2026-08.md")
        with open(path, encoding="utf-8") as f:
            body = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write("hand-edited preface\n" + body)
        code, out, _ = run("companion.py", "forget", "--entry", "2026-08-03", home=self.home)
        self.assertEqual(code, 3, out)       # a refusal, not an unknown-flag usage error
        self.assertIn("no longer matches", out)
        self.assertIn("第四条", _home_text(self.home))

    def _people_setup(self):
        run("companion.py", "consent", "--set", "relationships=yes", home=self.home)
        run("companion.py", "add-entry", "--date", "2026-08-10", "--text", "和小李吵架了",
            "--people", "小李", home=self.home)
        run("companion.py", "add-entry", "--date", "2026-08-11", "--text", "今天读书",
            home=self.home)
        run("companion.py", "cache", "--module", "relationships", "--merge-json", json.dumps(
            {"people": {"小李": {"incidents": [
                            {"date": "2026-08-10", "gist": "吵架", "lens": "criticism"}]},
                        "阿May": {"incidents": [
                            {"date": "2026-08-11", "gist": "聊天", "lens": "bid"}]}}}),
            home=self.home)
        run("companion.py", "continuity", "--merge-json", json.dumps(
            {"rolling_summary": "最近和小李关系紧张", "open_threads": [
                {"thread": "跟小李道歉", "opened": "2026-08-10", "status": "open"},
                {"thread": "读完那本书", "opened": "2026-08-11", "status": "open"}]}),
            home=self.home)

    def test_forget_person_with_entries_removes_them_everywhere(self):
        self._people_setup()
        r = jrun("companion.py", "forget", "--person", "小李", "--with-entries", home=self.home)
        self.assertTrue(r["ok"], r)
        text = _home_text(self.home)
        self.assertNotIn("小李", text)
        for keep in ("今天读书", "阿May", "读完那本书"):
            self.assertIn(keep, text)

    def test_forget_person_alone_lists_the_entries_that_still_mention_them(self):
        self._people_setup()
        r = jrun("companion.py", "forget", "--person", "小李", home=self.home)
        self.assertTrue(r["ok"], r)
        self.assertEqual([e["date"] for e in r["entries_still_mentioning"]], ["2026-08-10"])
        _, out, _ = run("companion.py", "journal", home=self.home)
        self.assertIn("和小李吵架了", out, "the prose stays until they ask for it too")

    def test_forget_relationships_removes_the_category(self):
        self._people_setup()
        r = jrun("companion.py", "forget", "--relationships", home=self.home)
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "state", "modules", "relationships.yaml")))
        self.assertTrue(all(row["people"] == [] for row in _index_rows(self.home)))
        self.assertFalse(jrun("companion.py", "status", home=self.home)
                         ["consent"]["relationships"])

    def test_forget_mood_strips_every_mood_value(self):
        import re
        run("companion.py", "consent", "--set", "mood=yes", home=self.home)
        for m in ("3", "7"):
            run("companion.py", "add-entry", "--text", "x", "--mood", m, home=self.home)
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"recent_moods": [3, 7]}), home=self.home)
        r = jrun("companion.py", "forget", "--mood", home=self.home)
        self.assertTrue(r["ok"], r)
        self.assertTrue(all(row["mood"] is None for row in _index_rows(self.home)))
        self.assertIsNone(re.search(r"mood \d+/10", _home_text(self.home)))
        self.assertEqual(jrun("companion.py", "brief", home=self.home)
                         ["continuity"]["recent_moods"], [])
        self.assertFalse(jrun("companion.py", "status", home=self.home)["consent"]["mood"])


class TestCareerScoringHasACommand(HomeCase):
    """Scoring a real person had no command. career.md told the model to import the module
    and pointed it at `score_person`, which ranks all 188 occupations even for an answer set
    with no shape — the refusal lives only in `score_person_grouped`. A model that followed
    the doc's own import line reported 'Strong' matches for someone who had answered
    'neutral' to all 21 items."""

    SHAPED = {str(i): (4 if i in (5, 6, 7, 8) else 1) for i in range(1, 22)}
    VALUES = ["Independence", "Achievement", "Working Conditions", "Recognition",
              "Support", "Relationships"]

    def _intake(self, answers, values_rank=None):
        latest = {"answers": answers, "values_rank": values_rank or {},
                  "answered": len(answers)}
        run("companion.py", "cache", "--module", "career_intake", "--merge-json",
            json.dumps({"latest": latest}), home=self.home)

    def test_scores_the_form_intake(self):
        self._intake(self.SHAPED, {v: i + 1 for i, v in enumerate(self.VALUES)})
        code, out, err = run("career_match.py", "--score-intake", home=self.home)
        self.assertEqual(code, 0, out + err)
        r = json.loads(out)
        self.assertTrue(r["numeric_interests"])
        self.assertTrue(r["code_only"])
        self.assertTrue([p for p in r["numeric_interests"] if "values" in p["components_used"]],
                        "a complete values ranking must engage the values blend")

    def test_person_facing_rows_carry_no_raw_scores(self):
        self._intake(self.SHAPED)
        r = json.loads(run("career_match.py", "--score-intake", home=self.home)[1])
        for row in r["numeric_interests"] + r["code_only"]:
            for k, v in row.items():
                self.assertNotIsInstance(v, float, f"{k}={v} leaks a raw score")

    def test_a_flat_answer_set_is_refused_loudly(self):
        self._intake({str(i): 2 for i in range(1, 22)})
        code, out, _ = run("career_match.py", "--score-intake", home=self.home)
        self.assertEqual(code, 3, out)
        r = json.loads(out)
        self.assertTrue(r["refused"])
        self.assertNotIn("numeric_interests", r)

    def test_no_intake_says_how_to_get_one(self):
        code, out, _ = run("career_match.py", "--score-intake", home=self.home)
        self.assertEqual(code, 2, out)
        self.assertIn("form_server.py --form career", out)

    def test_chat_collected_answers_score_the_same_way(self):
        code, out, err = run("career_match.py", "--answers", json.dumps(self.SHAPED),
                             "--values", ",".join(self.VALUES), home=self.home)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(json.loads(out)["numeric_interests"])

    def test_out_of_range_answers_are_rejected(self):
        bad = dict(self.SHAPED, **{"3": 9})
        code, out, _ = run("career_match.py", "--answers", json.dumps(bad), home=self.home)
        self.assertEqual(code, 2, out)
        self.assertIn("0..4", out)

    def test_one_occupation_can_be_asked_for_by_soc_code(self):
        self._intake(self.SHAPED)
        r = json.loads(run("career_match.py", "--score-intake", "--soc", "15-2041.00",
                           home=self.home)[1])
        self.assertEqual(r["occupation"]["onet_code"], "15-2041.00")
        self.assertIn(r["occupation"]["group"], ("numeric_interests", "code_only"))

    def test_the_doc_points_at_the_guarded_entry_point(self):
        with open(os.path.join(SKILL, "references", "modules", "career.md"),
                  encoding="utf-8") as f:
            doc = f.read()
        self.assertIn("--score-intake", doc)
        self.assertNotIn("from career_match import score_person", doc)


class TestComputeWithoutStoring(HomeCase):
    """「算一次，别存」 had no path: declining to store birth data meant no chart at all, while
    the destiny cache — the pillars, which are birth data in another form — could be written
    with no consent whatever. A chart computes from what was typed; storing it, the cache
    included, needs birth consent."""

    PILLARS = {"chart": {"pillars": "癸酉 丙辰 癸亥 丙辰"}}

    def test_the_destiny_cache_needs_birth_consent(self):
        code, out, _ = run("companion.py", "cache", "--module", "destiny", "--merge-json",
                           json.dumps(self.PILLARS), home=self.home)
        self.assertEqual(code, 3, out)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "state", "modules", "destiny.yaml")))

    def test_a_revoked_birth_consent_withholds_the_cached_chart(self):
        run("companion.py", "consent", "--set", "birth=yes", home=self.home)
        self.assertTrue(jrun("companion.py", "cache", "--module", "destiny", "--merge-json",
                             json.dumps(self.PILLARS), home=self.home)["ok"])
        run("companion.py", "consent", "--set", "birth=no", home=self.home)
        code, out, _ = run("companion.py", "cache", "--module", "destiny", home=self.home)
        self.assertEqual(code, 3, out)
        self.assertNotIn("癸亥", out)

    def test_onboarding_offers_computing_without_storing(self):
        with open(os.path.join(SKILL, "references", "onboarding.md"), encoding="utf-8") as f:
            self.assertIn("这次算一下", f.read())


class TestDailyCardReadsTheDayAgainstTheChart(unittest.TestCase):
    """Nearly half of all charts are near-balanced (193 of 400 random births), and for them
    the daily block carried no 喜/忌 on any day and empty 五行 tips for ever: what changed day
    to day was the 流日 十神, a ten-day cycle, and a single 生肖 relation. The day's branch
    against each of the person's own pillars is the arithmetic 合婚 already does between two
    charts, and it gives every chart a real, day-specific texture."""

    BIRTH = dict(date="1993-04-12", time="07:35", gender="m", tz="Asia/Shanghai")

    def daily(self, on, **kw):
        import bazi
        return bazi.compute(**dict(self.BIRTH, **kw), on_date=on)["computed"]["daily"]

    @staticmethod
    def days(n=60):
        import datetime
        start = datetime.date(2026, 9, 1)
        return [start + datetime.timedelta(days=i) for i in range(n)]

    def test_each_natal_pillar_is_read_against_the_day(self):
        d = self.daily(self.days(1)[0])
        self.assertEqual([x["pillar"] for x in d["natal_relations"]],
                         ["year", "month", "day", "hour"])

    def test_it_uses_the_same_relation_tables_as_synastry(self):
        import synastry
        for on in self.days(12):
            d = self.daily(on)
            zhi = d["liuri"]["ganzhi"][1]
            for x in d["natal_relations"]:
                self.assertEqual([r["relation"] for r in x["relations"]],
                                 [r["relation"] for r in
                                  synastry._relations_between(zhi, x["natal"][1])], (on, x))

    def test_a_clash_with_the_day_branch_shows_up(self):
        # the natal day pillar is 癸亥, and a 巳 day clashes 亥
        for on in self.days():
            d = self.daily(on)
            if d["liuri"]["ganzhi"][1] == "巳":
                row = next(x for x in d["natal_relations"] if x["pillar"] == "day")
                self.assertIn("六冲", [r["relation"] for r in row["relations"]])
                return
        self.fail("no 巳 day in 60 days")

    def test_the_same_ganzhi_as_a_natal_pillar_is_flagged(self):
        for on in self.days():
            d = self.daily(on)
            if d["liuri"]["ganzhi"] == "癸亥":
                row = next(x for x in d["natal_relations"] if x["pillar"] == "day")
                self.assertTrue(row["same_pillar"], row)
                return
        self.fail("no 癸亥 day in 60 days")

    def test_no_birth_hour_means_no_hour_row(self):
        d = self.daily(self.days(1)[0], time=None)
        self.assertNotIn("hour", [x["pillar"] for x in d["natal_relations"]])

    def test_the_relation_tables_live_in_one_place(self):
        import synastry
        try:
            import _branches
        except ImportError:
            self.fail("scripts/_branches.py is missing")
        self.assertIs(synastry._relations_between, _branches.relations_between)


class TestSustainedLowMood(HomeCase):
    """Crisis has a protocol and an ordinary day has a card; the long middle had nothing.
    `trend` could say `declining` and no document used it, so two weeks of 3/10 still got a
    fortune card every morning and nobody asked how they were doing."""

    def setUp(self):
        super().setUp()
        run("companion.py", "consent", "--set", "mood=yes", home=self.home)

    def _log(self, moods):
        import datetime
        today = datetime.date.today()
        for i, m in enumerate(moods):
            run("companion.py", "add-entry", "--date",
                (today - datetime.timedelta(days=i)).isoformat(), "--text", "嗯",
                "--mood", str(m), home=self.home)

    def test_two_weeks_of_mostly_low_moods_asks_for_a_check_in(self):
        self._log([2, 3, 3, 6, 2, 3])
        self.assertIn("_wellbeing_check", jrun("companion.py", "brief", home=self.home))
        self.assertTrue(jrun("companion.py", "trend", "--days", "30",
                             home=self.home)["sustained_low"]["triggered"])

    def test_four_entries_are_not_enough(self):
        self._log([2, 2, 2, 2])
        self.assertNotIn("_wellbeing_check", jrun("companion.py", "brief", home=self.home))

    def test_exactly_half_low_is_not_more_than_half(self):
        self._log([2, 3, 3, 7, 7, 7])
        self.assertNotIn("_wellbeing_check", jrun("companion.py", "brief", home=self.home))

    def test_it_asks_at_most_once_a_week(self):
        import datetime
        self._log([2, 3, 3, 6, 2, 3])
        run("companion.py", "continuity", "--merge-json",
            json.dumps({"wellbeing_checked": datetime.date.today().isoformat()}), home=self.home)
        self.assertNotIn("_wellbeing_check", jrun("companion.py", "brief", home=self.home))

    def test_without_mood_consent_nothing_is_inferred(self):
        self._log([2, 3, 3, 6, 2, 3])
        run("companion.py", "consent", "--set", "mood=no", home=self.home)
        self.assertNotIn("_wellbeing_check", jrun("companion.py", "brief", home=self.home))

    def test_the_note_is_care_not_a_crisis_script(self):
        self._log([2, 3, 3, 6, 2, 3])
        note = json.dumps(jrun("companion.py", "brief", home=self.home)["_wellbeing_check"],
                          ensure_ascii=False)
        self.assertNotRegex(note, r"\d{3,}", "no phone numbers outside a crisis")
        for word in ("抑郁", "depress"):
            self.assertNotIn(word, note)


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
