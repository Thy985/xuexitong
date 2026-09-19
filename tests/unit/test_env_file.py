"""utils.env_file.load_env_file —— .env 读取的唯一实现。

背景：scripts/ 下 8 份 .env 解析互不一致（mooc2_probe 认 `=` 和 `:`，
diag_v1_login 只认 `=`），而本项目 .env 实际是冒号格式；M0 入口 ci_local_run
原先根本不读 .env，只查 os.environ，与 LOCAL_FIRST_SETUP.md 的说明矛盾。
"""

from utils.env_file import load_env_file


class TestColonFormatIsTheProjectReality:
    def test_reads_colon_separated_pairs(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER: 18605440838\nCX_PASS: secret123\n",
                                       encoding="utf-8")
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "18605440838",
                                                "CX_PASS": "secret123"}

    def test_reads_equals_separated_pairs(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER=18605440838\n", encoding="utf-8")
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "18605440838"}

    def test_value_containing_equals_survives_colon_line(self, tmp_path):
        (tmp_path / ".env").write_text("TOKEN: a=b=c\n", encoding="utf-8")
        env: dict = {}
        load_env_file(tmp_path, env)
        assert env["TOKEN"] == "a=b=c", "按首个分隔符切，值里的 = 不能被再切一刀"


class TestRealEnvironmentWins:
    def test_existing_key_is_not_overridden(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER: from_file\n", encoding="utf-8")
        env = {"CX_USER": "from_shell"}
        assert load_env_file(tmp_path, env) == {}
        assert env["CX_USER"] == "from_shell"


class TestNoiseIsIgnored:
    def test_comments_and_blank_lines_skipped(self, tmp_path):
        (tmp_path / ".env").write_text("# note\n\n  \nCX_USER: u1\n", encoding="utf-8")
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "u1"}

    def test_line_without_separator_skipped(self, tmp_path):
        (tmp_path / ".env").write_text("garbage\nCX_USER: u1\n", encoding="utf-8")
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "u1"}

    def test_missing_file_is_not_an_error(self, tmp_path):
        env = {"CX_USER": "u1"}
        assert load_env_file(tmp_path, env) == {}
        assert env == {"CX_USER": "u1"}
