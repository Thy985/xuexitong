"""点级深读 URL 必须用**当前账号自己的** enc，不得硬编码原作者签名。

回归（feojfe5645 fork 实测，run 37007265348）：`read_chapter_job_points` 把原作者
的 `enc=1bc1bd…` 硬编码进深读 URL。enc/cpi/openc 都是按用户签发的签名——fork
用户的会话拿原作者的 enc 请求，服务端不渲染 knowledge/cards 帧（全部章
`cards_frames=0`）→ 全课程发现不了任何视频点 → 队列恒空 NOOP。任何 fork 用户
都必撞。修复：enc/openc/hidetype 从调用方（用户自己的课程 URL）透传。
"""

from tvdp.tdvp import read_chapter_job_points


AUTHOR_ENC = "1bc1bd778f9e00d924fe97b3c63f76f4"


class _UrlCapturingPage:
    """记录 goto URL 的假页面（无帧 → 读数返回空，但 URL 已可断言）。"""

    def __init__(self):
        self.urls: list[str] = []
        self.frames: list = []

    def goto(self, url, **kw):
        self.urls.append(url)

    def wait_for_timeout(self, *a, **k):
        pass


def test_point_read_url_uses_caller_enc_not_hardcoded_author_enc():
    page = _UrlCapturingPage()
    read_chapter_job_points(page, "1217304771", "265997861", "151695658",
                            "506830405", enc="8435290bc259b61452b1463db54e4aba")
    url = page.urls[0]
    assert "enc=8435290bc259b61452b1463db54e4aba" in url, \
        "深读 URL 必须带调用方（当前账号）自己的 enc"
    assert AUTHOR_ENC not in url, "不得再硬编码原作者的 enc"


def test_point_read_url_omits_enc_when_not_provided():
    page = _UrlCapturingPage()
    read_chapter_job_points(page, "1217304771", "265997861", "151695658",
                            "506830405")
    assert "enc=" not in page.urls[0], "无 enc 时省略参数，绝无硬编码兜底"


def test_point_read_url_carries_openc_and_hidetype_when_given():
    page = _UrlCapturingPage()
    read_chapter_job_points(page, "1217304771", "265997861", "151695658",
                            "506830405", enc="e" * 32,
                            openc="442a0c20e5959eb4effe10e17b716bce",
                            hidetype="0")
    url = page.urls[0]
    assert "openc=442a0c20e5959eb4effe10e17b716bce" in url
    assert "hidetype=0" in url


def test_fork_user_shape_end_to_end_url():
    """feojfe5645 fork 的真实形状：他的 cpi + 他的 enc 组合出现在同一 URL 里。"""
    page = _UrlCapturingPage()
    read_chapter_job_points(page, "1217304728", "265997861", "151695658",
                            "506830405", enc="8435290bc259b61452b1463db54e4aba")
    url = page.urls[0]
    assert "cpi=506830405" in url and "enc=8435290" in url
    assert "chapterId=1217304728" in url


# ── scheduler 接线：独立复核把 enc 一路带给 live_verify_chapter ──────

def test_probe_passes_url_enc_into_independent_verify(monkeypatch):
    from scheduler.scheduler import _PROBE_FETCH_CACHE, _run_tdvp_probe
    import tvdp.tdvp as T
    import app.registry.task_registry as R
    from app.registry.task_registry import TaskRecord

    store: dict = {}
    monkeypatch.setattr(R, "load_registry", lambda key: dict(store))
    monkeypatch.setattr(R, "save_registry", lambda key, reg: store.update(reg))
    monkeypatch.setattr(R, "save_queue", lambda key, q: None)
    monkeypatch.setattr(R, "load_chapter_points", lambda key: {})
    monkeypatch.setattr(R, "save_chapter_points", lambda key, pts: None)
    _PROBE_FETCH_CACHE.clear()

    seen = {}

    def _fake_live_verify(cid, course_id, clazz_id, cpi, user, pw,
                          enc="", **kw):
        seen["enc"] = enc
        return {"video_total": 1, "video_finished": 1, "live_pending": set(),
                "live_finished": {cid},
                "points": [{"task_id": cid, "type": "video",
                            "isFinished": True}]}

    monkeypatch.setattr(T, "live_verify_chapter", _fake_live_verify)
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {
                            "chapters": [{"chapter_id": "1217304771",
                                          "title": "万维网WWW",
                                          "status": "pending",
                                          "job_remaining": 3}],
                            "points": []})

    store["1217304719"] = TaskRecord("1217304719", "1217304719", "c",
                                     task_type="video", status="COMPLETED")

    _run_tdvp_probe(
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        "?chapterId=1217304771&courseId=265997861&clazzid=151695658"
        "&cpi=506830405&enc=8435290bc259b61452b1463db54e4aba&mooc2=1"
        "&hidetype=0", "k")

    assert seen.get("enc") == "8435290bc259b61452b1463db54e4aba", \
        f"独立复核必须收到课程 URL 里的 enc，实际 {seen!r}"
