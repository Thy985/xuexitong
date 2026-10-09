# 统一倍速配置：XUE_PLAYBACK_RATE 解析校验 + 引擎侧章内后续点倍速应用。
#
# 架构（2026-10-09 定案）：Python 引擎是倍速配置的唯一权威来源，v3 主链路与
# 章内后续点分别执行——主链路由 v3 读 window.__XUE_PLAYBACK_RATE__，章内后续点
# 由引擎在「src 含目标 objectid」的绑定帧上设置。两条路都不许"找不到目标就
# 回退控制别的视频"（v3 取第一个 video 正是把它钉死在点 1 的根因）。

import pathlib

from app.e2_headed_gha import (
    PLAYBACK_RATE_DEFAULT,
    PLAYBACK_RATE_MAX,
    PLAYBACK_RATE_MIN,
    apply_playback_rate,
    parse_playback_rate,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ── parse_playback_rate：XUE_PLAYBACK_RATE 解析与校验 ──────────────

def test_default_is_1_0():
    # 合规默认：不倍速。空值/缺省都回退 1.0。
    assert PLAYBACK_RATE_DEFAULT == 1.0
    assert parse_playback_rate(None) == 1.0
    assert parse_playback_rate("") == 1.0
    assert parse_playback_rate("   ") == 1.0


def test_valid_range_values_parse():
    assert parse_playback_rate("1.0") == 1.0
    assert parse_playback_rate("0.5") == 0.5
    assert parse_playback_rate("2.0") == 2.0
    assert parse_playback_rate("1.25") == 1.25
    assert parse_playback_rate("1.5") == 1.5
    assert parse_playback_rate("2") == 2.0
    assert parse_playback_rate(" 1.5 ") == 1.5


def test_non_numeric_falls_back_to_1_0():
    assert parse_playback_rate("abc") == 1.0
    assert parse_playback_rate("1,5") == 1.0
    assert parse_playback_rate("一倍速") == 1.0


def test_nan_and_inf_fall_back_to_1_0():
    assert parse_playback_rate("NaN") == 1.0
    assert parse_playback_rate("nan") == 1.0
    assert parse_playback_rate("inf") == 1.0
    assert parse_playback_rate("-inf") == 1.0
    assert parse_playback_rate("Infinity") == 1.0


def test_out_of_range_falls_back_to_1_0():
    assert parse_playback_rate("0.1") == 1.0
    assert parse_playback_rate("0.49") == 1.0
    assert parse_playback_rate("2.01") == 1.0
    assert parse_playback_rate("3.0") == 1.0
    assert parse_playback_rate("-1.0") == 1.0


def test_range_boundaries_are_accepted():
    assert parse_playback_rate(str(PLAYBACK_RATE_MIN)) == PLAYBACK_RATE_MIN
    assert parse_playback_rate(str(PLAYBACK_RATE_MAX)) == PLAYBACK_RATE_MAX


# ── apply_playback_rate：只在绑定帧上设置倍速，绝不碰别的视频 ──────
#
# 用最小假帧模拟 JS 契约：帧内 evaluate 依据自己 src 是否含 oid 返回
# {ok: true/false, reason: ...}。测试验证的是 Python 侧控制流：只有 src 含
# 目标 oid 的帧被判定为成功，找不到绑定帧时绝不回退到别的视频。


class _FakeFrame:
    def __init__(self, src: str, has_video: bool = True):
        self.src = src
        self.has_video = has_video
        self.calls = []  # 记录每次 evaluate 收到的 arg

    def evaluate(self, _js: str, arg: dict) -> dict:
        self.calls.append(arg)
        oid = arg.get("oid", "")
        if not self.has_video:
            return {"ok": False, "reason": "no_video"}
        if not self.src or oid not in self.src:
            return {"ok": False, "reason": "not_target"}
        return {"ok": True, "rate": arg.get("rate")}


class _FakePage:
    def __init__(self, frames):
        self.frames = frames


def _frames(srcs):
    return [_FakeFrame(s) for s in srcs]


def test_apply_only_touches_the_frame_whose_src_carries_target_oid():
    oid = "53d6b112bbbb"
    fr_point1 = _FakeFrame("https://x/ananas/19da22ccaaaa/enc.mp4")
    fr_target = _FakeFrame(f"https://x/ananas/{oid}/enc.mp4")
    page = _FakePage([fr_point1, fr_target])

    assert apply_playback_rate(page, oid, 1.5) is True
    # 两帧都被探测（身份判定在帧内 JS 做），但只有目标帧收到 rate=1.5 的 arg
    assert fr_point1.calls == [{"oid": oid, "rate": 1.5}]
    assert fr_target.calls == [{"oid": oid, "rate": 1.5}]


def test_apply_returns_false_when_no_frame_is_bound_to_target():
    # 找不到绑定帧 → False，绝不拿别的点的视频来设倍速（P1 病灶的反面）
    page = _FakePage(_frames(["https://x/ananas/19da22ccaaaa/enc.mp4"]))
    assert apply_playback_rate(page, "53d6b112bbbb", 1.5) is False


def test_apply_returns_false_without_frames_or_bad_args():
    assert apply_playback_rate(_FakePage([]), "53d6b112bbbb", 1.5) is False
    assert apply_playback_rate(_FakePage(_frames(["https://x/a/oid.mp4"])),
                               None, 1.5) is False
    assert apply_playback_rate(_FakePage(_frames(["https://x/a/oid.mp4"])),
                               "oid", None) is False


def test_apply_does_not_fall_back_when_target_frame_has_no_video():
    # 绑定帧存在但帧内无 <video> → False；不因"反正要设倍速"去控制别的帧
    fr_no_video = _FakeFrame("https://x/ananas/53d6b112bbbb/enc.mp4",
                             has_video=False)
    fr_other = _FakeFrame("https://x/ananas/19da22ccaaaa/enc.mp4")
    page = _FakePage([fr_no_video, fr_other])
    assert apply_playback_rate(page, "53d6b112bbbb", 1.5) is False


def test_apply_propagates_configured_rate_verbatim():
    oid = "53d6b112bbbb"
    page = _FakePage(_frames([f"https://x/ananas/{oid}/enc.mp4"]))
    assert apply_playback_rate(page, oid, 1.25) is True
    assert page.frames[0].calls == [{"oid": oid, "rate": 1.25}]


# ── v3 主链路回归锚：硬编码 1.5 已移除、改读外部配置并回退 1.0 ─────

def test_v3_userscript_reads_external_rate_and_defaults_to_1_0():
    src = (_ROOT / "scripts" / "v3_optimized.user.js").read_text(encoding="utf-8")
    # 不再写死 1.5
    assert "playbackRate: 1.5," not in src
    # 读引擎注入的前导配置；缺省/非法回退 1.0
    assert "window.__XUE_PLAYBACK_RATE__" in src
    assert "? r : 1.0;" in src
