# PROJECT-PASSPORT — xuexitong

> 项目自有的环境契约与自主边界。Company 侧只登记"本机能提供什么能力"
> （`D:\Company\state\capability-map.yaml`），本文件登记"本项目需要什么、怎么起、哪些不能碰"。
> 入职 Stage 4/5 产物；环境事实变化时**先改本文件**，再决定是否向 Company 发 REQ。

---

## 1. Identity

| 字段 | 值 |
|---|---|
| 项目 | `D:\Projects\Archive\xuexitong`（注意：在 Archive 区，仍在活跃开发） |
| 远程 | `github.com:Thy985/xuexitong`（私有，分支 `main`） |
| 语言/运行时 | Python 3.12（CI 与本地统一 3.12.13） |
| 核心依赖 | `app/requirements.txt` → `playwright==1.63.0`（**唯一**运行时依赖） |
| 测试依赖 | `pytest`、`pytest-cov`（未写进 requirements，见 §4 待办） |
| 形态 | 无 Web 前端；CLI + GitHub Actions 定时；有头浏览器驱动 |

## 2. Required Capabilities（映射到 Company capability-map）

| 项目需求 | Company 能力 | 状态 | 消费约束 |
|---|---|---|---|
| 有头浏览器自动化 | `browser-automation` | active | 用 **venv 内 `playwright` 包**（`from playwright.sync_api import ...`）。Company 的 `playwright` CLI 是 `uv tool` 1.63.0，**不是**项目入口；`browser-harness`/CDP 本项目不用 |
| Chromium 引擎 | 同上（chromium-1243） | active | 期望 revision 由 playwright 版本决定：1.63.0 → **chromium-1243**。升 pin 前先看缓存有无对应 revision，避免白下 350MB |
| 可换浏览器（Edge 兜底） | MS Edge branded channel | active | 经 `utils/browser_factory`：`XUE_BROWSER_CHANNEL=msedge` 或 `XUE_BROWSER_EXE` |
| Python 3.12 | `runtime-python` | present | `uv venv` **不含 pip**，装包用 `VIRTUAL_ENV=.venv uv pip install` |
| 真站访问 chaoxing | `network-diag` | present | ⚠️ 本机有 **dead system proxy `127.0.0.1:7897`**（Company Drift 遗留）。任何 urllib/requests/httpx 抓取需 `--noproxy '*'` 或 `no_proxy`；Playwright 侧注意 proxy 注入 |
| 读 CI 日志 / artifact | `cloud`(gh) | present | 需 `repo` scope 的 token；无 admin 时 `run view --log` 会 403 |
| 本地 Linux/Xvfb（L4 同构验收） | `wsl` | **present-but-idle** | 仅 L4「本地/云 verification_10 一致」需要；M0/M1 走 Windows 有头即可，**不阻塞** |
| 容器（可选） | `container` | missing | 本项目**无容器需求**，不构成缺口 |

## 3. Setup（一键，实测于 2026-09-19）

```bash
cd D:/Projects/Archive/xuexitong
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -r app/requirements.txt pytest pytest-cov
# 浏览器：先查 %LOCALAPPDATA%\ms-playwright\chromium-1243 是否存在，存在则跳过下载
.venv/Scripts/python.exe -m playwright install chromium   # 仅缺时执行
# L1 基线
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/unit tests/integration tests/regression -q
# L2 真站验收入口
.venv/Scripts/python.exe scripts/ci_local_run.py --action scheduler --trigger manual
```
凭据：`.env`（`CX_USER`/`CX_PASS`，`key: value` 冒号格式）或环境变量；会话缓存 `.cache/cookies.json`。
详见 `docs/runbooks/LOCAL_FIRST_SETUP.md`。

## 4. 自主边界（Agent 在本项目内可自决 / 必须停）

**可自决（Project Scope）**：改 `app/ scheduler/ tvdp/ utils/ tests/ scripts/ docs/`、
`.venv` 内装包、跑 pytest、更新本 Passport。

**须先问 / 走 Company Request**：
- 改 `.github/workflows/*`（影响定时与云端行为）
- `git push`、触发 `workflow_dispatch`
- **对真课程发起真实学习 run** —— 会改服务端完成态并写 `state/`，属外部可见副作用
- 启动 WSL / 装全局工具 / 改 PATH / 写 `D:\Company\state\`

**项目自身合规红线**（README，不可越）：只允许"真实浏览器自然播放 → 服务端自行判定完成"。
**不**构造/伪造/重放 `multimedia/log`，**不**改 `enc/attDurationEnc/videoFaceCaptureEnc/playingTime/_t`，
**不**跳过播放，**不**宣称整门课程自动化完成。
> 推论：`isPassed` 只能来自**首次真实响应的捕获**，不得靠重复 GET 上报端点获取。

**待办**：`app/requirements.txt` 只含 playwright，测试依赖与版本锁未入册（R-02 环境可复现的缺口）。
