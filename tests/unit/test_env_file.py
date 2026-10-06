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


class TestOverrideModeForLocalExe:
    """loop/exe 形态 .env 覆盖机器环境变量（CI 的默认语义在本地是坑）。

    实测事故：用户级环境变量残留 CX_USER=186…，.env 填的是 150… —— exe 静默
    用 186 登录，浏览器里填出的手机号和用户配置的完全对不上。
    """

    def test_override_lets_env_file_win(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER=15005478570\nCX_PASS=pw150\n",
                                       encoding="utf-8")
        env = {"CX_USER": "18605440838"}     # 模拟机器残留（无 CX_PASS）
        loaded = load_env_file(tmp_path, env, override=True)
        assert loaded == {"CX_USER": "15005478570", "CX_PASS": "pw150"}
        assert env["CX_USER"] == "15005478570"
        assert env["CX_PASS"] == "pw150"

    def test_override_without_file_keeps_shell_values(self, tmp_path):
        env = {"CX_USER": "from_shell", "CX_PASS": "from_shell"}
        assert load_env_file(tmp_path, env, override=True) == {}
        assert env == {"CX_USER": "from_shell", "CX_PASS": "from_shell"}

    def test_override_still_skips_empty_values(self, tmp_path):
        """override 也不能把模板空值注入进去 —— 那会把真凭据顶成 ""。"""
        (tmp_path / ".env").write_text("CX_USER=\nCX_PASS=pw150\n", encoding="utf-8")
        env = {"CX_USER": "18605440838"}
        loaded = load_env_file(tmp_path, env, override=True)
        assert loaded == {"CX_PASS": "pw150"}
        assert env["CX_USER"] == "18605440838"


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


class TestEmptyValuesAreNotInjected:
    """随包模板自带 `CX_USER=` 空键；若注入 ""，先到先得会永远遮住用户后填的
    真值 —— exe 二次启动反复要求输入账密的根因（2026-10-06 排查定案）。"""

    def test_empty_value_line_not_injected(self, tmp_path):
        (tmp_path / ".env").write_text("CX_USER=\nCX_PASS=\n", encoding="utf-8")
        env: dict = {}
        assert load_env_file(tmp_path, env) == {}
        assert env == {}

    def test_build_template_then_user_input_loads_real_credentials(self, tmp_path):
        """完整复现 exe 用户链路：build.py 模板 → loop 追加真凭据 → 二次启动。"""
        (tmp_path / ".env").write_text(
            "# 学习通账号（本地保存，勿上传/入库）\n"
            "CX_USER=\nCX_PASS=\n"
            "# XUE_LOOP_INTERVAL=30\n", encoding="utf-8")
        # 第一次启动：读不到（空值不注入）→ 引导输入 → loop 写入（修复后为合并写）
        env1: dict = {}
        assert load_env_file(tmp_path, env1) == {}
        from app.loop import _merge_env_file
        _merge_env_file(tmp_path / ".env", {"CX_USER": "18605440838",
                                            "CX_PASS": "pw123"})
        # 二次启动：必须读到真值
        env2: dict = {}
        loaded = load_env_file(tmp_path, env2)
        assert loaded == {"CX_USER": "18605440838", "CX_PASS": "pw123"}
        assert env2["CX_USER"] and env2["CX_PASS"]

    def test_file_still_works_after_merge_keeps_single_pair(self, tmp_path):
        """合并写不产生重复键：模板空键被原地更新，注释保留。"""
        from app.loop import _merge_env_file
        p = tmp_path / ".env"
        p.write_text("# 头注释\nCX_USER=\n# 中间注释\nCX_PASS=\nOTHER=keep\n",
                     encoding="utf-8")
        _merge_env_file(p, {"CX_USER": "u1", "CX_PASS": "p1"})
        text = p.read_text(encoding="utf-8")
        assert text.count("CX_USER=") == 1, "不得另起追加重复键"
        assert text.count("CX_PASS=") == 1
        assert "OTHER=keep" in text and "# 头注释" in text and "# 中间注释" in text
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "u1", "CX_PASS": "p1",
                                                "OTHER": "keep"}

    def test_merge_appends_keys_when_file_absent(self, tmp_path):
        from app.loop import _merge_env_file
        p = tmp_path / ".env"
        _merge_env_file(p, {"CX_USER": "u1", "CX_PASS": "p1"})
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "u1", "CX_PASS": "p1"}

    def test_merge_updates_colon_format_lines(self, tmp_path):
        """老用户手写的冒号格式键也要能被原地更新为 = 格式。"""
        from app.loop import _merge_env_file
        p = tmp_path / ".env"
        p.write_text("CX_USER:old\n", encoding="utf-8")
        _merge_env_file(p, {"CX_USER": "new"})
        env: dict = {}
        assert load_env_file(tmp_path, env) == {"CX_USER": "new"}
