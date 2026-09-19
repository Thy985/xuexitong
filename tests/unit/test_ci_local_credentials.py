"""ci_local_run 的凭据前置检查必须先补 .env，再判缺失。

事故（2026-09-19）：`ci_local_run.py --action scheduler` 直接 exit 2 报"缺少 CX_PASS"，
而 `.env` 里两个键都在。原因：前置检查只查 `os.environ`，从不读 `.env` —— 与
LOCAL_FIRST_SETUP.md「凭据写 .env」的说明互相矛盾，M0 入口在本地必然跑不起来。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ci_local_run  # noqa: E402


class TestCredentialsFallBackToEnvFile:
    def test_env_file_satisfies_both_keys(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER: u1\nCX_PASS: p1\n", encoding="utf-8")
        env: dict = {}
        assert ci_local_run.ensure_credentials(tmp_path, env) == []
        assert env["CX_USER"] == "u1" and env["CX_PASS"] == "p1"

    def test_partial_env_file_reports_only_the_gap(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER: u1\n", encoding="utf-8")
        env: dict = {}
        assert ci_local_run.ensure_credentials(tmp_path, env) == ["CX_PASS"]

    def test_no_env_file_reports_both(self, tmp_path):
        env: dict = {}
        assert ci_local_run.ensure_credentials(tmp_path, env) == ["CX_USER", "CX_PASS"]

    def test_shell_env_wins_over_file(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER: from_file\nCX_PASS: p\n", encoding="utf-8")
        env = {"CX_USER": "from_shell"}
        assert ci_local_run.ensure_credentials(tmp_path, env) == []
        assert env["CX_USER"] == "from_shell"

    def test_repo_env_file_actually_satisfies_the_check(self):
        """守本仓真实 .env 的格式约定（冒号）：它必须能被前置检查吃进去。"""
        if not (ci_local_run._REPO / ".env").exists():
            pytest.skip("本机无 .env（CI 上凭据走真环境变量）")
        env: dict = {}
        assert ci_local_run.ensure_credentials(ci_local_run._REPO, env) == [], \
            "本仓 .env 存在却判缺失 → loader 又和项目格式脱节了"
