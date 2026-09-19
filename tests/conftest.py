"""pytest configuration for xuexitong tests."""
import os
import sys
from pathlib import Path

import pytest

# 测试会话不得携带真账号凭据：scheduler 的时长探测 / live 复核会真起 headed
# Chromium 并调 ensure_login，凭据在时就会静默拿真实账号打真站（桌面反复弹登录窗、
# 一次几十秒）。需要凭据的用例自己 monkeypatch.setenv，作用域显式、不外溢。
for _k in ("CX_USER", "CX_PASS"):
    os.environ.pop(_k, None)

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
