"""登录态判据必须把「未登录」与「已登录但被 manage 权限门挡住」分开。

事故（2026-09-19 本地）：`_is_login_warning` 把 "暂无权限使用该后台" 也算成未登录，
于是 ensure_login 在**已经登录成功**之后又去重新登录一次；且它跳的是裸
`passport2.chaoxing.com/login`（不带 refer），登录后没有返回上下文 → 卡在登录页，
最终 login_ok=False。实测真相：登录后 v1/manage 渲染
「唐怀远 您暂无权限使用该后台，点击这里进个人空间」，
链接 href = https://i.chaoxing.com —— 缺的是**点这个链接**，不是再登一次。
"""

from utils.cookie_store import (
    PERSONAL_SPACE_SELECTORS,
    is_permission_gate,
    is_unauthenticated,
)

GATE_HTML = ("<html>唐怀远 您暂无权限使用该后台，"
             '<a href="https://i.chaoxing.com">点击这里进个人空间</a></html>')
NOT_LOGGED_IN_HTML = '<html><body>用户未登录</body></html>'
COURSE_HTML = '<html><body><div class="chapter">第4章 运输层</div></body></html>'


class TestPermissionGateIsNotUnauthenticated:
    def test_gate_page_detected(self):
        assert is_permission_gate(GATE_HTML) is True

    def test_gate_page_is_not_reported_as_unauthenticated(self):
        """这条正是原 bug：门页被当成未登录 → 触发多余的二次登录。"""
        assert is_unauthenticated(GATE_HTML) is False

    def test_course_page_is_neither(self):
        assert is_permission_gate(COURSE_HTML) is False
        assert is_unauthenticated(COURSE_HTML) is False


class TestUnauthenticatedStillDetected:
    def test_login_warning_detected(self):
        assert is_unauthenticated(NOT_LOGGED_IN_HTML) is True

    def test_empty_and_none_are_safe(self):
        assert is_unauthenticated("") is False
        assert is_permission_gate(None) is False


class TestPersonalSpaceLinkKnown:
    def test_selectors_cover_the_real_href(self):
        """实测 href 是 https://i.chaoxing.com；选择器必须覆盖它，否则点不到。"""
        assert any("i.chaoxing.com" in s for s in PERSONAL_SPACE_SELECTORS)

    def test_selectors_cover_the_visible_text(self):
        assert any("个人空间" in s for s in PERSONAL_SPACE_SELECTORS)
