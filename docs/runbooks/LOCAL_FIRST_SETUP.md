# LOCAL_FIRST_SETUP.md — xuexitong 本地起步 & M0 验收指引

> 目的：让任何人**从零**在本地装好环境、跑通 scheduler、产出可复现 evidence，
> 用于 M0「本地稳定」验收（ACCEPTANCE L2）。
> 与 `LOCAL_PLAYWRIGHT_RUNBOOK.md` 的关系：后者偏「验证/只读/真站原理」，本文档是
> **正式本地运行入口（M0 的 L2 验收）**，用 `scripts/ci_local_run.py` 而非手动调引擎。
> 目标：10 分钟内可起一个能 run 的本地环境（R-02）。

---

## 1. 前置检查（硬性）

| 项 | 要求 | 自查 |
|---|---|---|
| OS | 任一（本手册示例 Windows，GHA 为 Ubuntu） | `echo $OS` |
| Python | **3.12**（与 CI `pytest` 一致，见 `.github/workflows/test.yml`） | `python --version` |
| Git | 已完成 clone | `git -C . rev-parse HEAD` |

> 为什么 3.12：CI 锁 3.12（`test.yml` 断言 `sys.version_info[:2]==(3,12)`）。本地用 3.12 可保证
> 行为与 CI 一致，避免「本地过、CI 挂」的版本漂移。

---

## 2. 一键准备（装依赖）

```bash
cd <仓库根>

# 1) 建隔离环境（uv venv 不含 pip，装包用 uv pip）
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -r app/requirements.txt
#     == playwright==1.63.0

# 2) Playwright 浏览器引擎（本地没有会导致 ci_local_run 报 BROWSER_MISSING）
#    先看期望 revision 是否已在缓存：playwright 1.63.0 → chromium-1243
ls "$LOCALAPPDATA/ms-playwright" 2>/dev/null || dir "%LOCALAPPDATA%\\ms-playwright"
#    已有 chromium-1243 → 跳过下载；没有才执行：
python -m playwright install chromium

# 3) （可选）运行测试所需
VIRTUAL_ENV=.venv uv pip install pytest pytest-cov
```

**本地解释器统一用 `.venv/Scripts/python.exe`**（Windows）/ `.venv/bin/python`（Linux/mac），
不要用系统 python —— 系统 python 里没有 playwright。

**验证依赖就绪**：
```bash
python -c "from playwright.sync_api import sync_playwright as sp; \
with sp() as p: b=p.chromium.launch(headless=True); print('launch', b.version); b.close()"
```
- 报 `executable doesn't exist` → 重跑第 2 步。
- 报 `launch <version>` → ✅ 浏览器可用。

### §2.5 用系统浏览器（无需 `playwright install`，可选）

只要机器装有系统 Chrome / Edge，可**跳过**大的 chromium 下载，用系统浏览器跑：

```bash
# 用系统 Edge（本机 Win 自带）：
export XUE_BROWSER_CHANNEL=msedge
python scripts/ci_local_run.py --trigger manual --max-chapters 1

# 或指定某个浏览器 exe 精确路径（如 Chrome）：
# export XUE_BROWSER_EXE="C:/Program Files/Google/Chrome/Application/chrome.exe"
```

- 优先级：`XUE_BROWSER_EXE` > `XUE_BROWSER_CHANNEL` > 默认 chromium。
- 都不设 → 默认内置 chromium（GHA/CI 行为一致）。
- 实现：`utils/browser_factory.py`（`launch_kwargs()`），已接入核心链路（e2/scheduler/tvdp）。
- 验证：`XUE_BROWSER_CHANNEL=msedge` 后 run 仍能 launch（实测 headless 起 Edge 成功）。

> 说明：这是 R-08（可配浏览器）。默认保持 chromium 不变，仅在显式设置环境变量时切换，零侵入。

---

## 3. 配置凭据（不外泄）

`ci_local_run.py`（scheduler/run 模式）需要 `CX_USER` / `CX_PASS`（学习通手机号/密码）。

### 推荐：`.env`（本仓库已 gitignore）
仓库根已有 `.env`（见根目录清单）。在其中填：
```
CX_USER=<你的学习通手机号>
CX_PASS=<你的密码>
```
（不要 commit `.env` 到仓库；`cx_pass` 若动了 secrets 会 leak，gitignore 已兜底。）

### 或：环境变量（临时，适合脚本/CI）
```bash
export CX_USER="..." CX_PASS="..."
```

> 凭据只做真实登录；`ci_local_run.py` 透明通过，不会写死进脚本/日志。

---

## 4. 跑 M0 验收入口（L2）

```bash
# 单次 scheduler（从 state/active_course.json 自动读课程）
python scripts/ci_local_run.py --trigger manual --max-chapters 2

# 连续 3 次，验证「PASS 可复现 / 不重复 / 不卡死」（ACCEPTANCE M0 判据）
python scripts/ci_local_run.py --repeat 3

# 失败时把证据打包成 zip（R-03），可附 issue
python scripts/ci_local_run.py --collect-diagnostics
```

**成功判据（ACCEPTANCE L2）**：
- 输出汇总里每个 verdict 为 `PASS`（或 `NOOP`=确无待办而已）。
- `--repeat 3` 3 次都 PASS 且同一课程进度只前进、不重复、不卡死。
- 任一失败 `failure_stage` 非空（如 `BROWSER_MISSING`、`LOGIN_FAILED`…）。

**结果留痕**：单次结果追加在 `evidence/_logs/ci_local_runs.jsonl`（历史保留，不覆盖）。

---

## 5. 常见问题（针对 ci_local_run）

| 症状 | 处理 |
|---|---|
| `缺少环境变量: ['CX_USER','CX_PASS']`（退出码 2） | 用 §3 配置 `.env`/export |
| 汇总 `failure_stage=BROWSER_MISSING` | 未装 Playwright Chromium → §2 第 2 步 |
| `verdict=NOOP` 但 probe empty | 无 active course（先 initialize）或真无待办；见下 |
| `failure_stage=LOGIN_FAILED` | 凭据错 / 会话被踢；查 blocking 的验证码（见 runbook §3.4） |

**「NOOP」可能是「还没有 active course」**：首次运行前先初始化一门课：
```bash
python app/run.py --action initialize --course-url "<完整 studentstudy URL（含 enc/openc/hidetype）>"
```
（初始化会写 `state/active_course.json` 与 `state/courses/<key>.json`，之后再跑 scheduler 即可。）

---

## 6. 与既有文档的分工

| 你需要 | 看这里 |
|---|---|
| 本地起步 + M0 验收（本文档） | `LOCAL_FIRST_SETUP.md` |
| 验证码原理、mooc2 正确入口、真实登录 | `LOCAL_PLAYWRIGHT_RUNBOOK.md` |
| 纯逻辑回归（不需浏览器） | `python -m pytest tests/` + `.github/workflows/test.yml` |
| 需求 / 开发 / 验收体系 | `docs/REQUIREMENTS.md` / `architecture/DEVELOPMENT_PLAN.md` / `engineering-review/ACCEPTANCE.md` |

---

## 7. 里程碑状态

> 达成 M0 的门槛（摘自 ACCEPTANCE L2）：scheduler 本地跑通、PASS 可复现、失败归因准确。
> 本文档即 R-02（环境可复现）的落点；配合 `scripts/ci_local_run.py`（R-01）。
> 跑通后把结果记入 `docs/engineering-review/ACCEPTANCE.md` §4 验收记录表。