import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis, load_service


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("apiServer", self.dir, self.redis)
        self.m.redis_client = self.redis
        self.c = self.m.cookies
        self.client = self.m.app.test_client()
        (Path(self.dir) / "nico.txt").write_bytes(b"# Netscape\nTOPSECRETVALUE\n")

    def post(self, **body: object):  # noqa: ANN202
        return self.client.post("/download", json={"url": "https://x/1", **body})

    def test_login_required_without_profile(self) -> None:
        err = self.c.LoginRequiredError("login required")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err), \
                mock.patch("os._exit") as exit_:
            r = self.post()
            time.sleep(0.7)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json["error"], "login_required")
        self.assertEqual(r.json["reason"], "cookie_missing")
        exit_.assert_not_called()  # ログイン要求ではプロセスを再起動しない

    def test_login_required_marks_profile_expired(self) -> None:
        err = self.c.LoginRequiredError("login required")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post(auth_profile="nico")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json["reason"], "cookie_expired")
        self.assertEqual(r.json["auth_profile"], "nico")
        self.assertEqual(self.c.profile_state(self.redis, "nico"), "expired")

    def test_expired_profile_short_circuits_then_revalidates(self) -> None:
        self.c.mark_expired(self.redis, "nico")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[]) as probe:
            r = self.post(auth_profile="nico")
            self.assertEqual(r.status_code, 401)
            probe.assert_not_called()
            # cookie を入れ直すと再び probe される
            p = Path(self.dir) / "nico.txt"
            os.utime(p, (time.time() + 10, time.time() + 10))
            r = self.post(auth_profile="nico")
            self.assertEqual(r.status_code, 200)
            probe.assert_called_once()

    def test_login_url_in_401(self) -> None:
        from urllib.parse import parse_qs, urlsplit
        self.c.BROWSER_UI_URL = "http://h:8080"
        err = self.c.LoginRequiredError("login required")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post(auth_profile="nico")
        q = parse_qs(urlsplit(r.json["login_url"]).query)
        self.assertEqual(q, {"profile": ["nico"], "start_url": ["https://x/"]})
        # 未指定でも、開始 URL 入りの URL を返す
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post()
        self.assertEqual(parse_qs(urlsplit(r.json["login_url"]).query),
                         {"start_url": ["https://x/"]})

    def test_profiles_endpoint_login_url(self) -> None:
        self.c.BROWSER_UI_URL = "http://h:8080"
        r = self.client.get("/auth/profiles")
        self.assertEqual(r.json[0]["login_url"], "http://h:8080/?profile=nico")

    def post_url(self, url: str, **body: object):  # noqa: ANN202
        return self.client.post("/download", json={"url": url, **body})

    def test_auto_select_valid_profile(self) -> None:
        (Path(self.dir) / "niconico.txt").write_bytes(b"# Netscape\n")
        job = {"id": "a", "url": "u", "options": []}
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[job]) as probe:
            r = self.post_url("https://www.nicovideo.jp/watch/sm9")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(probe.call_args.args[4], "niconico")
        # 明示指定が優先される
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[job]) as probe:
            self.post_url("https://www.nicovideo.jp/watch/sm9", auth_profile="nico")
        self.assertEqual(probe.call_args.args[4], "nico")

    def test_auto_select_missing_or_expired_probes_without_cookie(self) -> None:
        job = {"id": "a", "url": "u", "options": []}
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[job]) as probe:
            r = self.post_url("https://www.nicovideo.jp/watch/sm9")   # 未保存
        self.assertEqual((r.status_code, probe.call_args.args[4]), (200, None))
        (Path(self.dir) / "niconico.txt").write_bytes(b"x")
        os.utime(Path(self.dir) / "niconico.txt", (1000, 1000))
        self.c.mark_expired(self.redis, "niconico")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[job]) as probe:
            r = self.post_url("https://www.nicovideo.jp/watch/sm9")   # 失効中でも公開動画は取れる
        self.assertEqual((r.status_code, probe.call_args.args[4]), (200, None))

    def test_auto_select_login_required_reasons(self) -> None:
        self.c.BROWSER_UI_URL = "http://h:8080"
        err = self.c.LoginRequiredError("login required")
        url = "https://www.nicovideo.jp/watch/sm9"
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post_url(url)
        self.assertEqual((r.status_code, r.json["reason"], r.json["auth_profile"]),
                         (401, "profile_unknown", "niconico"))
        self.assertIn("profile=niconico", r.json["login_url"])
        # 有効な cookie を自動で使ってログイン要求 → 失効として記録
        (Path(self.dir) / "niconico.txt").write_bytes(b"x")
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post_url(url)
        self.assertEqual((r.json["reason"], r.json["auth_profile"]), ("cookie_expired", "niconico"))
        self.assertEqual(self.c.profile_state(self.redis, "niconico"), "expired")
        # 失効中: cookie なしで解析し、ログイン要求なら失効として案内
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err) as probe:
            r = self.post_url(url)
        self.assertEqual((probe.call_args.args[4], r.json["reason"]), (None, "cookie_expired"))
        # 対応するプロファイルが無いサイト
        with mock.patch.object(self.m.function, "probe_and_build_jobs", side_effect=err):
            r = self.post_url("https://vimeo.com/1")
        self.assertEqual((r.json["reason"], r.json["auth_profile"]), ("cookie_missing", None))

    def test_unknown_profile(self) -> None:
        with mock.patch.object(self.m.function, "probe_and_build_jobs") as probe:
            r = self.post(auth_profile="ghost")
        self.assertEqual((r.status_code, r.json["reason"]), (401, "profile_unknown"))
        probe.assert_not_called()

    def test_other_probe_failure_unchanged(self) -> None:
        with mock.patch.object(self.m.function, "probe_and_build_jobs",
                               side_effect=RuntimeError("boom")), \
                mock.patch("os._exit") as exit_:
            r = self.post()
            time.sleep(0.7)
        self.assertEqual(r.status_code, 400)
        exit_.assert_called_once_with(1)

    def test_rejects_forbidden_options_and_bad_profile(self) -> None:
        for opts in ("-u a -p b", "--cookies /etc/passwd", "--netrc-cmd id"):
            r = self.post(options=opts)
            self.assertEqual(r.status_code, 400, opts)
            self.assertIn("not allowed", r.json["message"])
        self.assertEqual(self.post(auth_profile="../x").status_code, 400)
        self.assertEqual(self.post(auth_profile=5).status_code, 400)

    def test_profile_passed_to_job_and_queue(self) -> None:
        job = {"id": "a", "url": "https://x/1", "options": [], "auth_profile": "nico"}
        with mock.patch.object(self.m.function, "probe_and_build_jobs", return_value=[job]):
            r = self.post(auth_profile="nico")
        self.assertEqual(r.status_code, 200)
        queued = json.loads(self.redis.lists["ytdlp:queue"][0])
        self.assertEqual(queued["auth_profile"], "nico")

    def test_schedule_keeps_profile_and_requeues_on_login_required(self) -> None:
        self.client.post("/schedule", json={"url": "https://x/1", "auth_profile": "nico"})
        self.assertEqual(json.loads(self.redis.lists["ytdlp:requests"][0])["auth_profile"], "nico")
        self.redis.lpop = lambda k: self.redis.lists[k].pop(0) if self.redis.lists.get(k) else None
        self.c.mark_expired(self.redis, "nico")
        r = self.client.post("/download/scheduled", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(len(self.redis.lists["ytdlp:requests"]), 1, "予約が失われない")

    def test_profiles_endpoint_hides_content(self) -> None:
        r = self.client.get("/auth/profiles")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json[0]["profile"], "nico")
        self.assertNotIn("TOPSECRETVALUE", r.get_data(as_text=True))

    def test_probe_uses_temp_cookie_copy_and_writes_back(self) -> None:
        out = json.dumps({"id": "v1", "title": "t", "webpage_url": "https://x/v1"})
        seen = {}

        def fake_run(cmd, **_kw):  # noqa: ANN001, ANN202
            path = cmd[cmd.index("--cookies") + 1]
            seen["path"] = path
            Path(path).write_bytes(b"# Netscape\nREFRESHED\n")  # yt-dlp の更新を模擬
            return mock.Mock(stdout=out + "\n")

        with mock.patch("subprocess.run", side_effect=fake_run):
            jobs = self.m.function.probe_and_build_jobs(
                "https://x/1", [], None, None, "nico")
        self.assertEqual(jobs[0]["auth_profile"], "nico")
        self.assertNotEqual(seen["path"], str(Path(self.dir) / "nico.txt"))
        self.assertIn(b"REFRESHED", (Path(self.dir) / "nico.txt").read_bytes())

    def test_probe_login_required_error(self) -> None:
        import subprocess
        e = subprocess.CalledProcessError(
            1, ["yt-dlp"], stderr="ERROR: Sensitive content, login required. Use --cookies, ...")
        with mock.patch("subprocess.run", side_effect=e), \
                self.assertRaises(self.c.LoginRequiredError):
            self.m.function.probe_and_build_jobs("https://x/1", [], None, None, None)


if __name__ == "__main__":
    unittest.main()
