import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPTS = os.path.join(ROOT, "skills", "remote-job-hunter", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, SCRIPTS)

import fetch_jobs as fj  # noqa: E402
import tracker  # noqa: E402

NOW = "2026-10-15T12:00:00+00:00"


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


class FixtureRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = os.path.join(cls.tmp, "raw.json")
        cls.data = quiet(fj.main, ["--fixtures", FIXTURES, "--now", NOW, "--profile", os.path.join(FIXTURES, "profile.md"),
                                   "--days", "14", "--out", cls.out,
                                   "--merge", os.path.join(FIXTURES, "community.json")])
        cls.jobs = {j["id"]: j for j in cls.data["jobs"]}

    def test_every_source_parsed(self):
        for name in ("remotive", "remoteok", "weworkremotely", "himalayas", "jobicy", "workingnomads", "hn",
                     "greenhouse:acme", "lever:beta", "ashby:gamma", "merged:community.json"):
            self.assertGreater(self.data["sources"][name]["fetched"], 0, name)
        self.assertTrue(os.path.exists(self.out))

    def test_every_kept_job_has_iso_date_and_fields(self):
        for j in self.data["jobs"]:
            self.assertRegex(j["posted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
            for key in ("title", "url", "apply_url", "contacts", "how_to_apply", "scam_flags", "eligible", "prescore"):
                self.assertIn(key, j)

    def test_ranked_by_prescore(self):
        scores = [j["prescore"] for j in self.data["jobs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_drops(self):
        d = self.data["counts"]["dropped"]
        self.assertNotIn("remoteok:4", self.jobs)  # 44 days old
        self.assertGreaterEqual(d["too_old"], 1)
        self.assertNotIn("remotive:2", self.jobs)  # USA only
        self.assertFalse(any(j["source"] == "himalayas" for j in self.data["jobs"]))  # Bulgaria only
        self.assertGreaterEqual(d["ineligible"], 2)
        self.assertFalse(any("Sales" in j["title"] for j in self.data["jobs"]))  # exclude_words
        self.assertFalse(any(j["title"] == "Data Analyst" for j in self.data["jobs"]))  # irrelevant
        self.assertNotIn("lever:beta:b2", self.jobs)  # hybrid
        self.assertNotIn("greenhouse:acme:12", self.jobs)  # not remote
        self.assertEqual(d["no_date"], 1)  # the community find with no date

    def test_duplicate_keeps_company_board(self):
        self.assertIn("greenhouse:acme:11", self.jobs)
        self.assertFalse(any(j["source"] == "weworkremotely" for j in self.data["jobs"]))
        self.assertIn("https://weworkremotely.com/remote-jobs/acme-flutter-developer", self.jobs["greenhouse:acme:11"]["also_on"])

    def test_reddit_is_not_used(self):
        self.assertNotIn("reddit", self.data["sources"])
        self.assertFalse(any(j["source"] == "reddit" or "reddit.com" in j["url"] for j in self.data["jobs"]))
        self.assertEqual(self.data["sources"]["merged:community.json"]["fetched"], 2)  # the Reddit find is dropped

    def test_hn(self):
        hn = self.jobs["hn:201"]
        self.assertEqual(hn["company"], "Kilo")
        self.assertEqual(hn["title"], "Senior Flutter Developer")
        values = [c["value"] for c in hn["contacts"]]
        self.assertIn("hiring@kilo.dev", values)
        self.assertIn("https://jobs.lever.co/kilo/123", values)
        self.assertNotIn("hn:202", self.jobs)  # on-site only

    def test_contacts_from_posting(self):
        jobicy = self.jobs["jobicy:5"]
        self.assertIn({"type": "email", "value": "jobs@pacificapps.io", "from": "posting"}, jobicy["contacts"])
        self.assertEqual(jobicy["how_to_apply"], "email")
        lumen = self.jobs["web:lumen-1"]
        self.assertEqual(lumen["how_to_apply"], "email")
        self.assertIn("talent@lumenhealth.app", [c["value"] for c in lumen["contacts"]])

    def test_tracker_skips_seen_jobs(self):
        path = os.path.join(self.tmp, "tracker.csv")
        quiet(tracker.main, ["--tracker", path, "add", self.out])
        again = quiet(fj.main, ["--fixtures", FIXTURES, "--now", NOW, "--profile", os.path.join(FIXTURES, "profile.md"),
                                "--tracker", path, "--out", os.path.join(self.tmp, "raw2.json")])
        self.assertEqual(again["jobs"], [])
        self.assertGreater(again["counts"]["dropped"]["seen"], 0)


class Helpers(unittest.TestCase):
    def test_find_emails(self):
        self.assertEqual(fj.find_emails("mail jobs [at] acme [dot] com now"), ["jobs@acme.com"])
        self.assertEqual(fj.find_emails("mail jobs (at) acme (dot) io"), ["jobs@acme.io"])
        self.assertEqual(fj.find_emails("write to sam at acme dot dev please"), ["sam@acme.dev"])
        self.assertEqual(fj.find_emails("Jobs@Acme.com, jobs@acme.com."), ["jobs@acme.com"])
        self.assertEqual(fj.find_emails("logo@2x.png privacy@acme.com jobs@indeed.com"), [])

    def test_parse_ats_url(self):
        self.assertEqual(fj.parse_ats_url("https://boards.greenhouse.io/gitlab/jobs/123"), ("greenhouse", "gitlab"))
        self.assertEqual(fj.parse_ats_url("https://job-boards.greenhouse.io/acme/jobs/1"), ("greenhouse", "acme"))
        self.assertEqual(fj.parse_ats_url("https://jobs.lever.co/spotify/abc"), ("lever", "spotify"))
        self.assertEqual(fj.parse_ats_url("https://jobs.ashbyhq.com/linear/x/application"), ("ashby", "linear"))
        self.assertIsNone(fj.parse_ats_url("https://www.linkedin.com/jobs/view/1"))
        self.assertEqual(fj.parse_ats_url("https://jobs.lever.co/Sprinto/abc"), ("lever", "Sprinto"))  # case kept

    def test_each_scam_flag_fires(self):
        cases = {
            "pay_to_work": "There is a small registration fee of $50.",
            "crypto_or_check": "Salary is paid in bitcoin every week.",
            "money_mule": "You will receive packages and reship them.",
            "too_good_to_be_true": "No experience needed, earn $60 an hour.",
            "off_platform_contact_only": "Contact us on WhatsApp.",
            "free_email_for_company": "We are a leading global company. Email acmecorp.hr@gmail.com",
        }
        for flag, text in cases.items():
            rec = fj.make("web", flag, "Job", "", "https://example.com/" + flag, NOW, text)
            rec["contacts"] = fj.contacts_for(rec)
            self.assertIn(flag, fj.scam_flags(rec), flag)
        clean = fj.make("remoteok", "ok", "Engineer", "Acme", "https://example.com/ok", NOW,
                        "We build crypto wallets. Apply at https://jobs.lever.co/acme/1")
        clean["contacts"] = fj.contacts_for(clean)
        self.assertEqual(fj.scam_flags(clean), [])

    def test_eligibility(self):
        prof = fj.load_profile(os.path.join(FIXTURES, "profile.md"))

        def elig(loc, title="Flutter Developer", source="remotive"):
            return fj.eligibility(fj.make(source, "x", title, "", "u", NOW, "", location_raw=loc), prof)[0]
        self.assertEqual(elig("Worldwide"), "yes")
        self.assertEqual(elig("APAC"), "yes")
        self.assertEqual(elig("USA Only"), "no")
        self.assertEqual(elig("Portugal"), "no")
        self.assertEqual(elig("Remote"), "unknown")
        self.assertEqual(elig("Time zone: CET (+/- 3 hours)"), "unknown")
        self.assertEqual(elig("Worldwide", title="Flutter Developer (Hybrid)"), "no")
        self.assertEqual(elig("REMOTE (Worldwide) or ONSITE, Austin, TX", source="hn"), "yes")
        self.assertEqual(elig("Worldwide", title="Flutter Developer - US only"), "no")
        self.assertEqual(elig("China", title="Senior Backend Engineer - Asia", source="jobicy"), "no")  # location beats title

    def test_to_iso(self):
        self.assertEqual(fj.to_iso(1791941509), "2026-10-14T01:31:49+00:00")
        self.assertEqual(fj.to_iso(1791941509000), "2026-10-14T01:31:49+00:00")
        self.assertEqual(fj.to_iso("Thu, 15 Oct 2026 08:42:17 +0000"), "2026-10-15T08:42:17+00:00")
        self.assertEqual(fj.to_iso("2026-10-14T00:32:03-04:00"), "2026-10-14T04:32:03+00:00")
        self.assertIsNone(fj.to_iso("soon"))

    def test_word_hit_spellings(self):
        self.assertTrue(fj.word_hit("frontend", "software engineer (front-end)"))
        self.assertTrue(fj.word_hit("react", "react native dev"))
        self.assertFalse(fj.word_hit("react", "reactive systems"))


class TrackerTest(unittest.TestCase):
    def test_add_set_summary(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "t.csv")
        jobs = os.path.join(tmp, "jobs.json")
        with open(jobs, "w") as f:
            json.dump({"jobs": [{"id": "a:1", "title": "Flutter Dev", "company": "A", "url": "u", "score": 90,
                                 "contacts": [{"type": "email", "value": "jobs@a.io"}]}]}, f)
        quiet(tracker.main, ["--tracker", path, "add", jobs])
        quiet(tracker.main, ["--tracker", path, "add", jobs])
        rows = tracker.load(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contact"], "jobs@a.io")
        quiet(tracker.main, ["--tracker", path, "set", "a:1", "applied", "--note", "form"])
        self.assertEqual(tracker.load(path)[0]["status"], "applied")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tracker.main(["--tracker", path, "summary", "--followup-days", "0"])
        self.assertIn("follow up", out.getvalue())
        with self.assertRaises(SystemExit):
            quiet(tracker.main, ["--tracker", path, "set", "a:1", "maybe"])


if __name__ == "__main__":
    unittest.main()
