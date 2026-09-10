"""pytest configuration for xuexitong tests."""
import sys
from pathlib import Path

import pytest

# 确保模块可导入
REPO_ROOT = Path(__file__).parent.parent
for p in [REPO_ROOT / "resolvers", REPO_ROOT / "state", REPO_ROOT / "e2"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_path(request) -> Path:
    """返回 tests/fixtures 目录；子测试可用 `fixture_path / "dom/x.html"` 读取快照。"""
    return FIXTURES_DIR
