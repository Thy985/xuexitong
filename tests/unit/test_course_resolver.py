"""Unit tests for Course Resolver (E5)."""
import pytest
import sys
from pathlib import Path

# 确保能导入模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from resolvers.course_resolver import (
    resolve_course,
    detect_course_change,
    identity_key,
    CourseIdentity,
    IdentityResult,
)


# ── Fixtures ──────────────────────────────────────────────────────
VALID_URL = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&courseId=265997861&clazzid=151695658"
    "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
    "&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a"
)

# 同课程不同 URL 参数（chapterId 不同）
SAME_COURSE_DIFF_URL = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304712&courseId=265997861&clazzid=151695658"
    "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
    "&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a"
)

# 同课程 clazzId 大写（应被规范化为小写）
SAME_COURSE_UPPER_CASE = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&courseId=265997861&clazzId=151695658"
    "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
    "&mooc2=1"
)

DIFFERENT_COURSE_URL = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&courseId=999999999&clazzid=999999999"
    "&cpi=999999999&enc=abcdef&mooc2=1"
)

INVALID_URL_NO_COURSE = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&clazzid=151695658&cpi=506830460"
)

INVALID_URL_NO_CLAZZ = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&courseId=265997861&cpi=506830460"
)


# ── Tests: resolve_course ─────────────────────────────────────────
class TestResolveCourse:
    def test_valid_url_returns_ok(self):
        result = resolve_course(VALID_URL)
        assert result.status == "OK"
        assert result.identity is not None
        assert result.identity.course_id == "265997861"
        assert result.identity.clazz_id == "151695658"
        assert result.identity.cpi == "506830460"
        assert result.identity.raw_url == VALID_URL

    def test_valid_url_evidence_contains_key(self):
        result = resolve_course(VALID_URL)
        assert result.is_ok()
        ev = result.evidence
        assert "identity_key" in ev
        assert ev["identity_key"] == "265997861_151695658"

    def test_different_chapter_same_course(self):
        """相同 courseId+clazzId 但 chapterId 不同 → SAME_COURSE"""
        r1 = resolve_course(VALID_URL)
        r2 = resolve_course(SAME_COURSE_DIFF_URL)
        assert r1.is_ok() and r2.is_ok()
        assert r1.identity.key() == r2.identity.key()

    def test_uppercase_clazzid_normalized(self):
        """clazzId 大写应被规范化为小写 clazzid"""
        result = resolve_course(SAME_COURSE_UPPER_CASE)
        assert result.is_ok()
        assert result.identity.clazz_id == "151695658"

    def test_invalid_no_course_id(self):
        result = resolve_course(INVALID_URL_NO_COURSE)
        assert result.status == "INVALID"
        assert "missing courseId" in result.error

    def test_invalid_no_clazz_id(self):
        result = resolve_course(INVALID_URL_NO_CLAZZ)
        assert result.status == "INVALID"
        assert "missing clazzid" in result.error

    def test_none_url(self):
        result = resolve_course(None)
        assert result.status == "INVALID"

    def test_empty_url(self):
        result = resolve_course("")
        assert result.status == "INVALID"

    def test_to_dict_roundtrip(self):
        result = resolve_course(VALID_URL)
        d = result.to_dict()
        assert d["status"] == "OK"
        assert "identity" in d
        assert d["identity"]["course_id"] == "265997861"


# ── Tests: detect_course_change ───────────────────────────────────
class TestDetectCourseChange:
    def test_same_course_returns_same(self):
        active = CourseIdentity(
            course_id="265997861", clazz_id="151695658",
            cpi="506830460", title="test", raw_url=VALID_URL,
            resolved_at_utc="2026-01-01T00:00:00Z",
        )
        det = detect_course_change(SAME_COURSE_DIFF_URL, active)
        assert det.kind == "SAME_COURSE"
        assert det.current_identity is not None
        assert det.active_identity is not None

    def test_new_course_returns_new(self):
        det = detect_course_change(VALID_URL, None)
        assert det.kind == "NEW_COURSE"
        assert det.current_identity is not None
        assert det.active_identity is None

    def test_different_course_returns_changed(self):
        active = CourseIdentity(
            course_id="265997861", clazz_id="151695658",
            cpi="506830460", title="old", raw_url=VALID_URL,
            resolved_at_utc="2026-01-01T00:00:00Z",
        )
        det = detect_course_change(DIFFERENT_COURSE_URL, active)
        assert det.kind == "COURSE_CHANGED"
        assert det.current_identity.key() != det.active_identity.key()

    def test_invalid_url_returns_invalid(self):
        det = detect_course_change(INVALID_URL_NO_COURSE, None)
        assert det.kind == "INVALID"

    def test_to_dict(self):
        active = CourseIdentity(
            course_id="265997861", clazz_id="151695658",
            cpi="506830460", title="test", raw_url=VALID_URL,
            resolved_at_utc="2026-01-01T00:00:00Z",
        )
        det = detect_course_change(VALID_URL, active)
        d = det.to_dict()
        assert d["kind"] == "SAME_COURSE"
        assert "current_identity" in d


# ── Tests: identity_key ───────────────────────────────────────────
class TestIdentityKey:
    def test_key_format(self):
        iden = CourseIdentity(
            course_id="265997861", clazz_id="151695658",
            cpi="506830460", title="test", raw_url=VALID_URL,
            resolved_at_utc="2026-01-01T00:00:00Z",
        )
        assert identity_key(iden) == "265997861_151695658"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
