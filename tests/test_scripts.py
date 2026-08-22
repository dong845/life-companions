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
    if expect_ok and r.returncode not in (0, 1, 2):
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
                       "--format", "json")
        x = r["computed"]["cross_check_sxtwl"]
        if x.get("available"):
            self.assertIs(x["agrees"], True)
        self.assertEqual(r["ambiguities"], [])

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

    def test_warns_on_unglossed_ten_gods(self):
        r = self.check("你的七杀很旺，所以压力大。", "destiny")
        self.assertTrue(any(x["code"] == "unglossed-jargon" for x in r["findings"]))

    def test_warns_when_strength_is_stated_as_fact(self):
        r = self.check("你身弱，用神是金水。", "destiny")
        self.assertTrue(any(x["code"] == "unhedged-strength" for x in r["findings"]))

    def test_english_output_is_checked_too(self):
        r = self.check("You will definitely get the job — you are destined to succeed.",
                       "career")
        self.assertFalse(r["ok"])


class TestCareerMatch(unittest.TestCase):
    def test_selftest_passes(self):
        code, out, err = run("career_match.py", "--selftest")
        self.assertEqual(code, 0, out + err)
        self.assertIn("SELFTEST: OK", out)

    def test_onet_attribution_survives(self):
        with open(os.path.join(SKILL, "data", "career", "occupations.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        blob = json.dumps(data).lower()
        self.assertIn("o*net", blob)


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
