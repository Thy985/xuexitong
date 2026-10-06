"""env_file — `.env` 读取（本地开发用；CI 走真环境变量）。

为什么单独一个模块：`scripts/` 下原先有 8 份各写一遍的 .env 解析，且**互不一致** ——
`mooc2_probe.py` 认 `=` 和 `:`，`diag_v1_login.py` 只认 `=`。本项目 `.env` 实际是
`CX_USER: 186…` 冒号格式，于是只认 `=` 的那份静默读不到凭据。
M0 入口 `ci_local_run.py` 原先完全不读 .env、只查 `os.environ`，与
`LOCAL_FIRST_SETUP.md`「凭据写 .env」的说明矛盾。此处收敛为单一实现。
"""

from __future__ import annotations

import os
import pathlib


def load_env_file(root: pathlib.Path, env: "dict|None" = None,
                  override: bool = False) -> dict:
    """读 `<root>/.env` 注入 env（默认 os.environ），返回实际注入的键值。

    默认（CI 语义）：真实环境变量优先，已存在的键不覆盖，本地文件只补空。
    override=True：**文件值覆盖已有键** —— loop/exe 形态专用。.env 是双击用户
    唯一的显式配置，不能被机器上残留的环境变量顶掉（实测：用户级 CX_USER 残留
    让 exe 静默换成另一个账号登录，浏览器里填出的手机号和 .env 对不上）。
    空值行（如模板里的 `CX_USER=`）**不注入**：随包模板自带空键，若注入 `""`，
    先到先得语义会让它永远遮住用户后填的真值（exe 用户凭据反复要求输入的根因，
    2026-10-06 排查定案）。
    """
    target = os.environ if env is None else env
    f = pathlib.Path(root) / ".env"
    if not f.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw in f.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        seps = [i for i in (line.find("="), line.find(":")) if i > 0]
        if not seps:
            continue
        at = min(seps)
        key, value = line[:at].strip(), line[at + 1:].strip()
        if not key or not value:
            continue
        if not override and key in target:
            continue
        target[key] = value
        loaded[key] = value
    return loaded
