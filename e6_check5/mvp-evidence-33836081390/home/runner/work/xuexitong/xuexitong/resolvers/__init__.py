"""E5: Course Resolver — URL → Canonical Course Identity"""
from .course_resolver import (
    resolve_course,
    IdentityResult,
    CourseIdentity,
    ChangeDetection,
)

__all__ = [
    "resolve_course",
    "IdentityResult",
    "CourseIdentity",
    "ChangeDetection",
]
