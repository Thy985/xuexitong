r"""故障诊断链路单测：runlog tee / 版本戳 / 脱敏 / 诊断摘要。

为什么这些需要钉住：
  - 脱敏正则改错过一次（`\d{4}(?!\d)` 只匹配 7 位，11 位手机号全漏，
    反而把 ct=1861234 这类计数打码，污染诊断证据本身）。两侧实现
    （Python 摘要 / PowerShell 收集脚本）必须保持同一套语义。
  - tee 装在 loop 主路径上，装坏了 = 整个常驻模式无输出，必须能安全退回。
  - 版本戳读不到时不能抛异常，否则本地 exe 直接起不来。
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from utils import diag_summary, runlog, version


# ── 脱敏 ────────────────────────────────────────────────────────────

class TestRedact:
    @pytest.mark.parametrize("raw,masked", [
        ("CX_USER=18612345678", "CX_USER=186****5678"),
        ("手机 13812345678 绑定", "手机 138****5678 绑定"),
        ("13900001111", "139****1111"),
        ("19998765432", "199****5432"),
    ])
    def test_phone_masked(self, raw, masked):
        assert diag_summary.redact(raw) == masked

    @pytest.mark.parametrize("raw", [
        "ct=655978",                        # 6 位计数
        "count=1861234",                    # 7 位——旧正则会误伤这里
        "ts=1789883761140",                 # 13 位时间戳
        "objectid=3d14e2c9406951936260b18",  # 长 hex
        "chapter=1217304708",               # 10 位章 id
    ])
    def test_non_phone_numbers_untouched(self, raw):
        """误伤比漏掉更糟：打码时间戳/objectid 会让证据自相矛盾。"""
        assert diag_summary.redact(raw) == raw

    def test_enc_token_masked(self):
        out = diag_summary.redact(
            "studentstudy?courseId=1&enc=SECRETVALUE123&clazzid=9")
        assert "SECRETVALUE123" not in out
        assert "enc=<REDACTED>" in out
        assert "courseId=1" in out and "clazzid=9" in out

    def test_cdn_tokens_masked(self):
        out = diag_summary.redact(
            "https://s2.cldisk.com/sd.mp4?at_=1789883761140&ak_=329a20d5986f&ad_=a993")
        assert "329a20d5986f" not in out
        assert "1789883761140" not in out
        assert "a993" not in out
        assert out.count("<REDACTED>") == 3

    def test_redact_obj_preserves_structure(self):
        src = {"raw_url": "x?enc=abc123def", "ct": 655978, "ok": True, "n": 3}
        out = diag_summary.redact_obj(src)
        assert out["raw_url"] == "x?enc=<REDACTED>"
        assert out["ct"] == 655978      # 数值不动
        assert out["ok"] is True
        assert out["n"] == 3

    def test_empty_input(self):
        assert diag_summary.redact("") == ""


# ── 版本戳 ──────────────────────────────────────────────────────────

class TestVersion:
    def test_app_version_reads_version_file(self):
        v = version.app_version()
        assert v and v != ""
        # 仓库根必有 VERSION；读不到时退回 0.0.0-dev 而不是抛异常
        assert isinstance(v, str)

    def test_build_info_keys(self):
        bi = version.build_info()
        for k in ("app_version", "git_sha", "built_at_utc", "executable", "frozen"):
            assert k in bi
        assert isinstance(bi["frozen"], bool)

    def test_never_raises_on_missing_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr(version, "resource_version_root", lambda: tmp_path)
        assert version.app_version() == version._FALLBACK
        assert version.git_sha() == ""   # 无 .git → 空串，不抛


# ── runlog tee ──────────────────────────────────────────────────────

class TestRunLog:
    def test_tee_writes_to_both(self, tmp_path):
        primary = io.StringIO()
        sink_path = tmp_path / "loop.log"
        sink = open(sink_path, "a", encoding="utf-8")
        tee = runlog._Tee(primary, sink)
        tee.write("hello loop\n")
        tee.flush()
        assert "hello loop" in primary.getvalue()
        sink.close()
        assert "hello loop" in sink_path.read_text(encoding="utf-8")

    def test_tee_survives_dead_sink(self):
        """日志盘满/句柄失效时不能连带打死控制台输出。"""
        primary = io.StringIO()

        class Dead:
            def write(self, _):
                raise OSError("disk full")
            def flush(self):
                raise OSError("disk full")

        tee = runlog._Tee(primary, Dead())
        tee.write("still visible\n")   # 不抛
        assert "still visible" in primary.getvalue()

    def test_tee_keeps_isatty(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        tee = runlog._Tee(TTY(), None)
        assert tee.isatty() is True

    def test_install_creates_parent_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "deep" / "loop.log"
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        fh = runlog.install_run_log(target)
        assert fh is not None
        assert target.parent.is_dir()
        fh.close()

    def test_install_opt_out(self, monkeypatch):
        monkeypatch.setenv("XUE_LOG_FILE", "0")
        assert runlog.env_opt_out() is True
        monkeypatch.setenv("XUE_LOG_FILE", "1")
        assert runlog.env_opt_out() is False

    def test_read_tail_missing_file(self, tmp_path):
        assert runlog.read_tail(tmp_path / "nope.log") == []

    def test_read_tail_limits(self, tmp_path):
        p = tmp_path / "l.log"
        p.write_text("\n".join(f"line{i}" for i in range(500)), encoding="utf-8")
        tail = runlog.read_tail(p, 10)
        assert len(tail) == 10
        assert tail[-1] == "line499"


# ── 诊断摘要 ────────────────────────────────────────────────────────

def _write_evidence(root, chapter="1217304708", stage="PLAYBACK_STALLED"):
    ev_dir = root / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "result": {
            "target": {"chapter_id": chapter},
            "verdict": "FAIL",
            "failure_stage": stage,
            "crash": None,
            "passed_count": 3,
            "retry_count": 1,
            "timing_s": 42.5,
            "created_at_utc": "2026-10-04T00:00:00+00:00",
        },
        "evidence": {
            "meta": {"chapter_id": chapter, "app_version": "0.4.0"},
            "checks": {"login_ok": True, "studentstudy_loaded": True,
                       "cards_iframe_loaded": False, "playback_started": True,
                       "isPassed_seen": False},
            "max_currentTime": 0.4,
            "video_duration": 575,
            "ml_log_count": 0,
            "errors": ["boom"],
            "diagnostics": {"at_end_console_tail": [{"text": "Uncaught TypeError"}]},
        },
    }
    (ev_dir / f"chapter_{chapter}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_registry(root, task_id="1217304708", status="BLOCKED", cf=3, stage="PLAYBACK_STALLED"):
    reg = root / "state" / "accounts" / "abc" / "registry" / "1_2"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "tasks.json").write_text(json.dumps({
        task_id: {
            "task_id": task_id, "chapter_id": task_id, "title": "点对点协议PPP",
            "status": status, "consecutive_failures": cf,
            "failure": {"stage": stage},
            "last_failure_at_utc": "2026-10-04T00:00:00+00:00",
        }
    }), encoding="utf-8")


class TestDiagSummary:
    def test_collects_failure_stage(self, tmp_path):
        _write_evidence(tmp_path)
        rows = diag_summary.collect_evidence(tmp_path)
        assert len(rows) == 1
        assert rows[0]["failure_stage"] == "PLAYBACK_STALLED"
        assert rows[0]["app_version"] == "0.4.0"

    def test_reports_failed_checks_only(self, tmp_path):
        _write_evidence(tmp_path)
        checks = diag_summary.collect_evidence(tmp_path)[0]["failed_checks"]
        assert "cards_iframe_loaded" in checks
        assert "login_ok" not in checks      # True 不该报为失败

    def test_collects_blocked_tasks(self, tmp_path):
        _write_registry(tmp_path)
        rows = diag_summary.collect_registry(tmp_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "BLOCKED"
        assert rows[0]["consecutive_failures"] == 3
        assert rows[0]["failure_stage"] == "PLAYBACK_STALLED"

    def test_summary_contains_all_three_sections(self, tmp_path):
        _write_evidence(tmp_path)
        _write_registry(tmp_path)
        (tmp_path / "evidence" / "loop.log").write_text(
            "[loop] 本轮结果：decision=BLOCKED result=FAILED\n", encoding="utf-8")
        text = diag_summary.summarize(tmp_path)
        assert "章节证据" in text
        assert "任务账本" in text
        assert "loop.log" in text
        assert "PLAYBACK_STALLED" in text
        # stage 释义要给出「最可能原因」，否则等于没汇总
        assert "最可能原因" in text

    def test_summary_handles_empty_root(self, tmp_path):
        """全新安装、一章没跑过 —— 不能抛异常。"""
        text = diag_summary.summarize(tmp_path)
        assert "未找到" in text
        assert len(text) > 100

    def test_summary_redacts_secrets_in_log(self, tmp_path):
        _write_evidence(tmp_path)
        (tmp_path / "evidence" / "loop.log").write_text(
            "登录 CX_USER=18612345678 url enc=TOPSECRET123\n", encoding="utf-8")
        text = diag_summary.summarize(tmp_path)
        assert "18612345678" not in text
        assert "TOPSECRET123" not in text
        assert "186****5678" in text

    def test_summary_never_leaks_raw_url_enc(self, tmp_path):
        _write_evidence(tmp_path)
        reg = tmp_path / "state" / "accounts" / "abc" / "courses"
        reg.mkdir(parents=True, exist_ok=True)
        (reg / "c.json").write_text(json.dumps({
            "course_identity": {"raw_url": "studentstudy?enc=LEAKME123&cpi=1"}
        }), encoding="utf-8")
        text = diag_summary.summarize(tmp_path)
        assert "LEAKME123" not in text

    def test_corrupt_json_does_not_crash(self, tmp_path):
        ev = tmp_path / "evidence"
        ev.mkdir(parents=True)
        (ev / "chapter_broken.json").write_text("{not json", encoding="utf-8")
        _write_evidence(tmp_path)
        rows = diag_summary.collect_evidence(tmp_path)
        assert len(rows) == 1        # 只收到好的那个
        assert diag_summary.summarize(tmp_path)


class TestDiagSummaryCli:
    """`python -m utils.diag_summary <目录>` —— 维护者拿到用户交来的 zip 后的入口。

    之前这个模块只有 summarize()、没有任何调用方，等于写了却用不上；
    补上 CLI 后「收到包 → 一条命令出结论」这条链路才真正闭合。"""

    def test_cli_writes_output_file(self, tmp_path, capsys):
        _write_evidence(tmp_path)
        out = tmp_path / "摘要.md"
        rc = diag_summary.main([str(tmp_path), "-o", str(out)])
        assert rc == 0
        assert "PLAYBACK_STALLED" in out.read_text(encoding="utf-8")

    def test_cli_prints_to_stdout_without_o(self, tmp_path, capsys):
        _write_evidence(tmp_path)
        assert diag_summary.main([str(tmp_path)]) == 0
        assert "PLAYBACK_STALLED" in capsys.readouterr().out

    def test_cli_honors_loop_log_lines(self, tmp_path):
        _write_evidence(tmp_path)
        log = tmp_path / "evidence" / "loop.log"
        log.write_text("\n".join(f"line-{i}" for i in range(200)), encoding="utf-8")
        text = diag_summary.summarize(tmp_path, loop_log_lines=10)
        assert "line-199" in text
        assert "line-100" not in text

    def test_cli_missing_dir_returns_2(self, tmp_path):
        assert diag_summary.main([str(tmp_path / "nope")]) == 2

class TestBuildProductClean:
    """打包产物的隐私护栏。

    背景：PyInstaller 的 COLLECT 会先删掉整个 dist/Xuexitong 再重建，所以
    「文件不存在才写模板」这种事后判断来不及生效 —— 早先的 build.py 每次重建
    都会把用户 .env 凭据和 .cache Cookie 冲掉。改成「先暂存」之后又走到另一个
    极端：还原回产物，于是每次打包都把构建者的账号进度/登录 Cookie/明文密码
    一起发给别的用户。后者比前者严重得多 —— 发出去就收不回来。

    所以契约是：**默认构建产物必须干净**，本机数据留在暂存目录；只有显式
    --keep-data 才放回。这几条把它钉死。
    """

    @staticmethod
    def _load_build_module(monkeypatch, dist_dir):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_build_under_test", str(__import__("pathlib").Path(__file__)
                                     .resolve().parents[2] / "build.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.DIST = str(dist_dir)
        return mod

    @staticmethod
    def _make_dirty_dist(dist_dir):
        """造一个「跑过 exe、带满本机数据」的 dist。"""
        import os
        for d in ("state", "evidence", ".cache"):
            os.makedirs(os.path.join(str(dist_dir), d, "sub"), exist_ok=True)
        write = lambda rel, txt: open(os.path.join(str(dist_dir), rel), "w",
                                      encoding="utf-8").write(txt)
        write(".env", "CX_USER=13800001111\nCX_PASS=RealPassword\n")
        write(os.path.join(".cache", "cookies-a.json"), '{"c":"TOKEN"}')
        write(os.path.join("state", "sub", "tasks.json"), "{}")
        write(os.path.join("evidence", "chapter_1.json"), "{}")

    def test_stash_moves_data_out_and_does_not_restore_by_default(self, tmp_path):
        import os
        import shutil
        self._make_dirty_dist(tmp_path)
        build = self._load_build_module(None, tmp_path)

        saved, restore = build.stash_user_data()
        # 模拟 COLLECT：整个目录删掉重建
        shutil.rmtree(str(tmp_path))
        os.makedirs(str(tmp_path))
        # 默认路径：调用方不调 restore()，产物就是干净的
        assert not os.path.exists(os.path.join(str(tmp_path), ".env"))
        assert not os.listdir(str(tmp_path))
        # 但数据没丢，还原函数仍可用
        restore()
        assert open(os.path.join(str(tmp_path), ".env"),
                    encoding="utf-8").read().startswith("CX_USER=13800001111")
        assert os.path.exists(os.path.join(str(tmp_path), ".cache", "cookies-a.json"))
        assert os.path.exists(os.path.join(str(tmp_path), "state", "sub", "tasks.json"))
        assert os.path.exists(os.path.join(str(tmp_path), "evidence", "chapter_1.json"))

    def test_verify_clean_product_passes_on_clean_dist(self, tmp_path):
        import os
        build = self._load_build_module(None, tmp_path)
        for d in ("state", "evidence", ".cache"):
            os.makedirs(os.path.join(str(tmp_path), d))
        open(os.path.join(str(tmp_path), ".env"), "w", encoding="utf-8").write(
            "CX_USER=\nCX_PASS=\n")
        build.verify_clean_product()      # 不抛 = 通过

    def test_verify_clean_product_rejects_credentials(self, tmp_path):
        import os
        build = self._load_build_module(None, tmp_path)
        for d in ("state", "evidence", ".cache"):
            os.makedirs(os.path.join(str(tmp_path), d))
        open(os.path.join(str(tmp_path), ".env"), "w", encoding="utf-8").write(
            "CX_USER=13800001111\nCX_PASS=RealPassword\n")
        with pytest.raises(SystemExit) as e:
            build.verify_clean_product()
        assert "CX_PASS" in str(e.value)

    def test_verify_clean_product_rejects_leftover_state(self, tmp_path):
        import os
        build = self._load_build_module(None, tmp_path)
        self._make_dirty_dist(tmp_path)
        with pytest.raises(SystemExit) as e:
            build.verify_clean_product()
        msg = str(e.value)
        assert "state/ 非空" in msg and "evidence/ 非空" in msg

    def test_verify_clean_product_skipped_under_keep_data(self, tmp_path):
        build = self._load_build_module(None, tmp_path)
        self._make_dirty_dist(tmp_path)      # 脏的
        build.verify_clean_product(keep_data=True)   # 但显式要了就放行
