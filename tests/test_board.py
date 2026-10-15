import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "skills", "remote-job-hunter", "scripts"))

import board  # noqa: E402
import tracker  # noqa: E402

JOBS = [
    {"id": "hn:1", "rank": 1, "score": 80, "title": "Flutter Dev </script><script>alert(1)</script>", "company": "Lemon.io",
     "source": "hn", "posted_at": "2026-10-14T00:00:00+00:00", "how_to_apply": "email", "url": "https://news.ycombinator.com/item?id=1",
     "apply_url": "https://news.ycombinator.com/item?id=1", "why": "Flutter", "gaps": "none",
     "contacts": [{"type": "email", "value": "jobs@lemon.io", "from": "posting"}]},
    {"id": "lever:x:2", "rank": 2, "score": 60, "title": "Mobile Engineer", "company": "Beta", "source": "lever",
     "posted_at": "2026-10-13T00:00:00+00:00", "how_to_apply": "form", "url": "javascript:alert(1)",
     "apply_url": "https://jobs.lever.co/x/2/apply", "contacts": []},
]


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.runs = os.path.join(self.tmp, ".remote-job-hunter")
        run = os.path.join(self.runs, "2026-10-16")
        os.makedirs(os.path.join(run, "lemon-io"))
        with open(os.path.join(run, "jobs.json"), "w") as f:
            json.dump({"jobs": JOBS}, f)
        with open(os.path.join(run, "lemon-io", "cover-letter.md"), "w") as f:
            f.write("Hi Lemon.io team,\n\nI shipped **3 Flutter apps**.")
        open(os.path.join(run, "lemon-io", "A-Lemon-Resume.pdf"), "w").close()
        self.profile = os.path.join(self.tmp, "profile.md")
        with open(self.profile, "w") as f:
            f.write("full_name: Test User\nemail: test@example.com\n")
        self.tracker = os.path.join(self.tmp, "tracker.csv")
        jobs_file = os.path.join(run, "jobs.json")
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.main(["--tracker", self.tracker, "add", jobs_file])

    def page(self):
        return board.render(self.runs, self.tracker, self.profile)

    def data(self):
        page = self.page()
        blob = page.split('<script id="data" type="application/json">')[1].split("</script>")[0]
        return json.loads(blob.replace("<\\/", "</"))

    def test_job_text_cannot_break_out_of_the_page(self):
        page = self.page()
        self.assertNotIn("</script><script>alert(1)", page)
        self.assertEqual(self.data()["jobs"][0]["title"], JOBS[0]["title"])

    def test_unsafe_urls_dropped(self):
        beta = next(j for j in self.data()["jobs"] if j["company"] == "Beta")
        self.assertEqual(beta["url"], "")
        self.assertEqual(beta["applyUrl"], "https://jobs.lever.co/x/2/apply")

    def test_email_job_has_drafts_with_cover_letter(self):
        lemon = next(j for j in self.data()["jobs"] if j["company"] == "Lemon.io")
        self.assertTrue(lemon["mailto"].startswith("mailto:jobs@lemon.io?subject="))
        self.assertTrue(lemon["gmail"].startswith("https://mail.google.com/mail/?view=cm&fs=1&to=jobs@lemon.io"))
        self.assertIn("I shipped 3 Flutter apps.", lemon["body"])  # Markdown stripped
        self.assertTrue(lemon["body"].startswith("Hi Lemon.io team,"))
        self.assertIn("Test User\ntest@example.com", lemon["body"])
        self.assertTrue(lemon["resume"].endswith("A-Lemon-Resume.pdf"))

    def test_slug(self):
        self.assertEqual(board.slug("Lemon.io"), "lemon-io")
        self.assertEqual(board.slug("Enveritas (YC S18)"), "enveritas-yc-s18")

    def test_status_from_tracker(self):
        tracker.set_status(self.tracker, "lever:x:2", "applied")
        beta = next(j for j in self.data()["jobs"] if j["company"] == "Beta")
        self.assertEqual(beta["status"], "applied")

    def test_build_writes_file(self):
        with contextlib.redirect_stdout(io.StringIO()):
            out = board.main(["build", "--runs", self.runs, "--tracker", self.tracker, "--profile", self.profile])
        self.assertTrue(os.path.exists(out))

    def test_serve_saves_status(self):
        args = type("A", (), {"runs": self.runs, "tracker": self.tracker, "profile": self.profile, "port": 0})
        srv = board.make_server(args)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            self.assertIn("Remote job board", urllib.request.urlopen(base + "/").read().decode())
            req = urllib.request.Request(base + "/api/status", data=json.dumps({"id": "hn:1", "status": "applied"}).encode(),
                                         headers={"Content-Type": "application/json"})
            self.assertEqual(json.load(urllib.request.urlopen(req)), {"ok": True})
            self.assertEqual(next(r for r in tracker.load(self.tracker) if r["id"] == "hn:1")["status"], "applied")
            bad = urllib.request.Request(base + "/api/status", data=b"id=hn:1&status=skipped",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(bad)
            self.assertEqual(next(r for r in tracker.load(self.tracker) if r["id"] == "hn:1")["status"], "applied")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_tailor_queue_and_listen(self):
        self.assertIsNone(board.request_tailor(self.runs, self.tracker, self.profile, "nope"))
        self.assertEqual(board.request_tailor(self.runs, self.tracker, self.profile, "lever:x:2"), "queued")
        self.assertEqual(board.request_tailor(self.runs, self.tracker, self.profile, "lever:x:2"), "queued")
        with open(board.queue_path(self.runs)) as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # a second click does not queue twice
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            board.main(["listen", "--runs", self.runs, "--once"])
        self.assertIn("TAILOR lever:x:2", out.getvalue())
        self.assertTrue(board.listening(self.runs))
        with contextlib.redirect_stdout(io.StringIO()):
            board.main(["tailor-state", "--runs", self.runs, "lever:x:2", "done"])
        beta = next(j for j in self.data()["jobs"] if j["company"] == "Beta")
        self.assertEqual(beta["tailor"], "done")

    def test_tailored_files_listed(self):
        lemon = next(j for j in self.data()["jobs"] if j["company"] == "Lemon.io")
        labels = {f["label"]: f["path"] for f in lemon["files"]}
        self.assertEqual(labels["Resume PDF"], "2026-10-16/lemon-io/A-Lemon-Resume.pdf")
        self.assertEqual(labels["Cover letter"], "2026-10-16/lemon-io/cover-letter.md")
        self.assertEqual(lemon["tailor"], "done")  # a cover letter on disk counts as tailored

    def test_server_tailor_and_files(self):
        with open(os.path.join(self.tmp, "secret.md"), "w") as f:
            f.write("outside")
        args = type("A", (), {"runs": self.runs, "tracker": self.tracker, "profile": self.profile, "port": 0})
        srv = board.make_server(args)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            req = urllib.request.Request(base + "/api/tailor", data=json.dumps({"id": "hn:1"}).encode(),
                                         headers={"Content-Type": "application/json"})
            res = json.load(urllib.request.urlopen(req))
            self.assertEqual((res["ok"], res["state"]), (True, "queued"))
            state = json.load(urllib.request.urlopen(base + "/api/state"))
            self.assertEqual(state["state"]["hn:1"]["state"], "queued")
            body = urllib.request.urlopen(base + "/files/2026-10-16/lemon-io/cover-letter.md").read().decode()
            self.assertIn("3 Flutter apps", body)
            for bad in ("/files/../secret.md", "/files/%2e%2e/secret.md", "/files/2026-10-16/jobs.json"):
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + bad)
            self.assertEqual(len(json.load(urllib.request.urlopen(base + "/api/data"))["jobs"]), 2)
        finally:
            srv.shutdown()
            srv.server_close()

if __name__ == "__main__":
    unittest.main()
