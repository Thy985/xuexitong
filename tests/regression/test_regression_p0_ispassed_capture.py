"""P0-04 续：isPassed 必须来自**首次真实响应体**，不得靠重复 GET 上报端点。

事故（run 35265696173 / 2026-09-17）：引擎已用 page.on("response") 监到真实
/mooc-ans/multimedia/log 响应，但只记了 url/t/status、**没记 body**；判定改为事后
对该 URL 二次 GET。结果 isPassed_seen=False 且 isPassed_body=null，而同一次 run 的
结束态截图显示该节侧栏已打勾 —— 无法区分"没学成"与"没测到"。

本组用例锁三件事：
  1. 判定读的是真实响应体（有 body 即命中，与二次 fetch 无关）；
  2. 探测记录必须落盘（可诊断），不再只存一个 count；
  3. 二次 GET 只在显式开诊断时发生（默认不重复请求上报端点 = 合规红线）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.e2_headed_gha import (  # noqa: E402
    has_is_passed_marker,
    ml_probe_records,
    read_event_body,
    refetch_requested,
)


class TestMarkerScannableOnCapturedBodies:
    """捕获到的真实响应体必须可被同一谓词判定（否则等于白捕）。"""

    def test_true_body_in_capture_is_detected(self):
        events = [
            {"url": "u1", "t": 1.0, "status": 200, "body": '{"isPassed":false}'},
            {"url": "u2", "t": 2.0, "status": 200, "body": '{"isPassed":true}'},
        ]
        hits = [r for r in ml_probe_records(events) if has_is_passed_marker(r["body"])]
        assert [h["url"] for h in hits] == ["u2"]

    def test_capture_without_marker_yields_no_hit(self):
        events = [
            {"url": "u1", "t": 1.0, "status": 200, "body": '{"isPassed":false}'},
            {"url": "u2", "t": 2.0, "status": 200, "body": None},
        ]
        assert [r for r in ml_probe_records(events) if has_is_passed_marker(r["body"])] == []


class TestProbeRecordsArePersisted:
    def test_records_keep_body_not_just_count(self):
        events = [
            {"url": "u1", "t": 1.0, "status": 200, "body": '{"isPassed":false}'},
            {"url": "u2", "t": 2.0, "status": 200, "body": '{"isPassed":true}'},
        ]
        recs = ml_probe_records(events)
        assert len(recs) == 2
        assert {r["status"] for r in recs} == {200}
        assert [r["body"] for r in recs] == ['{"isPassed":false}', '{"isPassed":true}']
        assert [r["url"] for r in recs] == ["u1", "u2"]

    def test_read_error_is_persisted_too(self):
        recs = ml_probe_records(
            [{"url": "u1", "t": 1.0, "status": 200, "body": None,
              "body_err": "ERR:Response text: Frame was detached"}])
        assert "Frame was detached" in recs[0]["body_err"], \
            "读体失败必须进证据，否则又退化成 isPassed_body=null 无从归因"


class TestRefetchIsOptInOnly:
    def test_disabled_by_default(self):
        assert refetch_requested({}) is False

    def test_enabled_only_by_explicit_flag(self):
        assert refetch_requested({"XUE_DIAG_REFETCH": "1"}) is True

    def test_falsy_values_do_not_enable(self):
        assert refetch_requested({"XUE_DIAG_REFETCH": "0"}) is False
        assert refetch_requested({"XUE_DIAG_REFETCH": ""}) is False


class TestRealResponseBodyReadOnce:
    """引擎侧读体接缝：真实响应体只读一次，失败要留下可见痕迹。"""

    class _Resp:
        def __init__(self, text=None, boom=False):
            self._text, self._boom, self.calls = text, boom, 0

        def text(self):
            self.calls += 1
            if self._boom:
                raise RuntimeError("response body unavailable")
            return self._text

    def test_reads_first_real_response_body(self):
        r = self._Resp('{"isPassed":true}')
        ev = {"resp": r}
        assert read_event_body(ev) == '{"isPassed":true}'
        assert r.calls == 1

    def test_cached_and_not_read_again(self):
        r = self._Resp("body")
        ev = {"resp": r}
        read_event_body(ev)
        read_event_body(ev)
        assert r.calls == 1, "重复读 Playwright Response 会抛，且属多余 I/O"

    def test_read_failure_is_recorded_not_swallowed(self):
        ev = {"resp": self._Resp(boom=True)}
        assert read_event_body(ev) is None
        assert ev["body_err"].startswith("ERR:"), "静默失败正是 isPassed_body=null 的成因"

    def test_missing_response_yields_none(self):
        assert read_event_body({}) is None
