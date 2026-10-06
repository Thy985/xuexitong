# xuexitong — 学习通自然学习 MVP

> 对超星学习通（chaoxing）课程，**Fork → 设置 Secrets → Initialize → Scheduler / TDVP 探针 → GitHub Actions 定时**，
> 系统按计划自动唤醒 Runtime，基于持久化状态和任务队列决定执行/跳过，输出可审计的 **Evidence**。

**重要边界**：本项目仅做"真实浏览器自然播放 → 服务端完成"，**不**构造/伪造/重放
`multimedia/log`、不修改 `enc/attDurationEnc/videoFaceCaptureEnc/playingTime/_t`、
不跳过播放、不宣称"整门课程自动化完成"。一次 Run 只自然完成 URL 中指定的一个视频任务点
（scheduler 模式可通过 `--max-chapters N` 一次跨章节推进，
手动 `workflow_dispatch` 的 `max_chapters` 输入即可填 2/3/…，默认 1）。
> 当前活跃课程处于 `BLOCKED` 时采用**熔断 cooldown 自动复位**：自动 (`schedule`) 触发下每累计
> `blocked_retry_interval`（默认 4，可用环境变量 `XUE_BLOCKED_RETRY_INTERVAL` 覆盖）次调度机会才自动放行一次
> 探测/重试；手动 (`manual`) 触发不受 cooldown 限制，可立即干预。恢复成功后熔断计数自动清零。

---

## 目录导航（先看这里）

这份 README 讲"怎么用"（快速开始 / 调度 / 本地调试）。按用途去找，代码与文档分层如下。

### 各目录是什么

| 目录 / 文件 | 放什么 |
|---|---|
| `app/` | **产品/运行时**：`run.py`（入口）、`e2_headed_gha.py`（headed-browser 引擎）、`registry/`（task_registry / reconcile / click_probe）、`probe_catalog.py` |
| `scheduler/` | **调度决策引擎**：`determine_action() / run_scheduler() / record_result()`；TDVP 探针内置于此 |
| `tvdp/` | **探针与验证协议**：`PassiveProbe / ActiveProbe / EvidenceAggregator` |
| `state/` | 持久化课程状态（active / courses / tdvp_tasks / registry），git 跨 Run 保留 |
| `tests/` | `unit/` 纯函数、`integration/` 调度+registry 持久化、`regression/` 历史事故回归（含真实 DOM fixture） |
| `scripts/` | 本地诊断/验证脚本：`mooc2_probe.py`、`capture_fixtures.py`、`diag_login.py`、`local-pw_probe.py`、`e3_ci_run.py` 等 |
| `docs/` | 工程文档（见下"文档地图"） |
| `config/`、`utils/` | 配置与工具 |

### 文档地图（docs/）

| docs/ 子目录 | 找什么 |
|---|---|
| `docs/architecture/` | 架构说明、状态机、scheduler/registry 设计 + **E3/E5/E6/E7 实验报告**（`E6_scheduler_report.md`、`E7_tdvp_report.md` 等） |
| `docs/engineering-review/` | **工程审计**：`ACTION_HISTORY_AUDIT.md`（运行考古）、`HISTORICAL_BUG_CASES.md`（历史事故）、`REGRESSION_MATRIX.md`（回归矩阵）、`AGENT_RULE_CANDIDATES.md`、`CI_GATES_CANDIDATES.md` |
| `docs/runbooks/` | **操作手册**：`LOCAL_PLAYWRIGHT_RUNBOOK.md`、`LOCAL_CAPABILITY_MATRIX.md`、`CAPTCHA_HANDLER_NOTES.md` |
| `docs/USER_MANUAL.md` | **使用手册**：本地 exe 操作、配置参考、错误速查、FAQ（打包后随 dist 分发为 `使用手册.md`） |
| `docs/evidence/` | **运行证据**：E2E 基线、真实登录/抓取快照（`mooc2_evidence/`）、`snapshot-archive/`（收敛的 CI 诊断抽样）、`E{5,6,7}_evidence.json`、历史 CI 日志 |

### 常见任务 → 去哪个链接

| 你想 | 看这里 |
|---|---|
| 本地 exe 怎么用 / 报错了怎么办 | `docs/USER_MANUAL.md`（使用手册） |
| 为什么某 run 全绿但 `progress.completed=0`？ | `docs/engineering-review/ACTION_HISTORY_AUDIT.md`；`docs/architecture/PROGRESS_OUTCOME_DATAFLOW.md`（五层 outcome） |
| 这套测试/回归体系怎么建、P0 项怎样分布 | `docs/engineering-review/REGRESSION_MATRIX.md`、`docs/engineering-review/TEST_SYSTEM_DESIGN.md` |
| 本地起浏览器做真实 E2E 验证 | `docs/runbooks/LOCAL_PLAYWRIGHT_RUNBOOK.md`、`scripts/mooc2_probe.py` |
| 超星滑块验证码怎么处理 | `docs/runbooks/CAPTCHA_HANDLER_NOTES.md`；代码 `utils/captcha_slider.py` |
| 新子功能/状态机想放哪 | `docs/architecture/README.md`（按四大分类就近归档） |
| 目录结构总览 | 下方 [`## 目录结构`](#目录结构) |

> 目录树（完整文件清单）见下文 `## 目录结构`；更细的 docs 地图见 `docs/README.md`。

---

## 快速开始（3 步）

### 1. Fork 本仓库

在 GitHub 上复制本仓库到你的账号。

### 2. 配置 Secrets

仓库 → **Settings → Secrets and variables → Actions**，添加两个 secret：

| Secret    | 说明            |
|-----------|----------------|
| `CX_USER` | 超星学习通账号（手机号） |
| `CX_PASS` | 超星学习通密码      |

```bash
gh secret set CX_USER -b "你的手机号"
gh secret set CX_PASS -b "你的密码"
```

### 3. 初始化 / 自检

> **fork 后第一步**：仓库 → **Actions** 页签，如提示 Workflows 被禁用请手动 **Enable**。
> GitHub 默认不对 fork 启用 Actions；启用后 `test` 工作流随 push/PR 自动跑（离线测试，
> 不需要 secrets）。

**标准流程（推荐）**：仓库 → **Actions** → 选中 `run` → **Run workflow**：

- `action`: **initialize**
- `course_url`（必填）：学习通**章节 studentstudy URL**，需含
  `chapterId` / `courseId` / `clazzid` / `cpi` / `enc`。**请从浏览器地址栏直接复制**
  （务必保留末尾的 `hidetype=0&openc=...`——缺失时服务端不渲染视频 iframe，引擎会如实上报 `FAIL(no_cards_frame)`）。例如：
  ```
  https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304708&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a
  ```

initialize 只激活课程；随后再跑一次 `action: **scheduler**`，调度内置 P0-3
**服务端真源 bootstrap**：该账号命名空间里课程账为空时，自动从**你账号的**服务端
catalog 材料化全部未完成章的 work 列表并写 `progress.completed`，之后每轮自选一个
视频任务点推进。

> 为什么 fork 用户**必须先 initialize**：账号隔离（P0-2）后，你的活跃课程存放在
> `state/accounts/<你的账号哈希>/active_course.json` —— 它不随 fork 存在。仓库里
> 那份 `state/active_course.json` 是原作者的 legacy 数据，你的登录账号读不到它；
> 直接跑 `scheduler` 会得到 NOOP（"No active course configured, run initialize
> first"），这是预期行为，不是故障。

**只想先自检「这门课我能不能跑」**：`action: **bootstrap**` + 同一 `course_url`。只做一次服务端
材料化并打印 `server_completed` / 任务数（幂等：该账号该课程已材料化过 → `NOOP`），不触发完整
调度。低门槛自检入口。

**账本对齐 / 恢复**：若账本疑似被污染或陈旧（如早期版本误继承过他人 legacy 账），
可显式以服务器为准重建（仅清**当前账号本课程**的 registry，不影响其他账号/课程）：
本地 `python scripts/bootstrap_p03_real.py --course-url "<同上>" --force`。

初始化完成后，你的账号命名空间下的 `active_course.json` 和
`courses/<course_id>_<clazz_id>.json` 会自动提交到 main 分支。

> **账号隔离（P0-2）**：从工作目录里登录账号 `CX_USER` 起，本机 run/switch/scheduler 的
> 全部状态都落到 `state/accounts/<account_id>/` 命名空间（account_id = 对 `CX_USER` 的确定性
> 哈希），同一课程的**不同账号互不可见**；cookie 缓存同样按账号隔离
> （`.cache/cookies-<account_id>.json`，同机切换账号不会误用他人会话）。
> 无登录（离线/诊断）时仍写旧的 `state/courses/` 与 `state/registry/<key>/`。
> **fork 原作者的旧账本（legacy 裸路径 + 原作者账号命名空间）不会被绑定成你的状态**：
> legacy 自动继承默认**关闭**（原作者本机迁移可显式 `XUE_INHERIT_LEGACY=1` 打开），
> fork 用户一律走服务端真源 bootstrap，从自己账号的真实完成度出发。
> **CI 上未配置 Secrets 时 scheduler/run 会拒绝运行**（exit 2），而不是落到原作者
> legacy 账本上跑并提交污染。
>
> **服务端真源（P0-3）**：账号首次进入课程（该账号命名空间里该课程 registry 为空）时，
> `bootstrap_registry_from_server` 从**服务端 catalog** 一次性材料化 work 列表，并把
> `progress.completed` 写成**服务端已完成章数**（而非本地 registry 的 done 数——旧行为会让
> `completed` 只反映“本地做过次数”）。任一已非空不重跑（幂等，不覆盖）。scheduler 首次进入
> 自动触发；`action=bootstrap` 可单独自检。注意：fork 账号**必须能进入**你要跑的那门课
> （学习通按账号鉴权，代码无法绕过）。
### 4. Scheduler（自动学习，内置 TDVP 探针）

**手动触发（一次）**：
- `action`: **scheduler**
- 无需传 `course_url`（自动从 `state/active_course.json` 读取）
- 无需传 `chapter_id`（TDVP 探针自动发现下一个待执行任务）
- 可选 `max_chapters: 2/3/...`：一次调度自动跨章节推进 N 个视频任务点（默认 1）。
  手动触发不受 `BLOCKED` cooldown 限制，可立即干预。

**自动定时（每天 UTC 02:00）**：无需手动操作，Workflow 内置 `schedule` trigger。
课程处于 `BLOCKED` 时，自动触发遵循 cooldown 自动复位（见开头说明），而非永远卡死。

Scheduler 内部自动执行：
1. 从 `state/active_course.json` 读取活跃课程
2. **TDVP Passive Probe**（后台静默）：扫描目录，深读队首附近 Top-K 候选章（`XUE_PROBE_TOPK`，默认 3），点亮任务队列；已验证无视频工作的章跨轮记忆跳过，窗口只前移不停滞
3. 读取课程状态决定本次是否执行（RUN / NOOP / BLOCKED / ERROR）
4. 若 RUN，自动选择下一个 pending 任务，调用浏览器 Runtime 执行学习
5. 更新并持久化 state 到 main 分支

> 失败分类（2026-10-06）：登录/会话/页面框架类**环境级失败**不计入章节失败预算，
> 本轮终止退避（`decision=ERROR`）；BLOCKED 只由真实播放类失败累积产生。

**用户只需 2 步**：
```bash
# ① Initialize（一次性，创建课程状态）
gh workflow run run.yml \
  -f action=initialize \
  -f course_url="https://mooc1.chaoxing.com/..."

# ② Scheduler（永久自动，什么都不用传）
gh workflow run run.yml -f action=scheduler
# 或等待 cron 每日自动触发
```

### 6. 切换课程（可选）

如需学习另一门课程：
- `action`: **switch**
- `course_url`：新课程 URL

系统会自动归档旧课程状态，激活新课程，后续 Scheduler 将自动跟随新课程。

---

## 产物（Evidence + 诊断）

Run 完成后，Actions 日志自动打印结构化诊断，并生成 artifact **`mvp-evidence-<run_id>`**：

| 产物 | 说明 |
|------|------|
| `evidence/result.json` | 信封（verdict/passed_count/failure_stage）+ 完整 Evidence |
| `state/` | 持久化课程状态（跨 Run 有效） |
| `state/tdvp_tasks.json` | TDVP 任务注册表（章节→任务状态） |
| `app/` | 产品代码 |
| `/tmp/diag_*.png` | 失败时截图 |

**Scheduler 输出字段**：
```json
{
  "action": "scheduler",
  "decision": "RUN|NOOP|BLOCKED",
  "result": "SUCCESS|NOOP|BLOCKED|FAILED",
  "trigger": "manual|schedule",
  "course_key": "265997861_151695658",
  "timing_s": 792.5,
  "verdict": "PASS|FAIL|...",
  "error": null
}
```

**TDVP 内置于 Scheduler**：用户无需单独调用，每次 scheduler 运行时自动在后台执行 Passive Probe，扫描任务状态并更新 `state/tdvp_tasks.json`。

---

## 失败诊断

MVP 在每次失败时自动给出结构化诊断，直接打印到 Actions 日志（`SCHEDULER DIAGNOSTICS` 块：action/decision/result/verdict/failure_stage 等）；失败场景自动保存截图到 `/tmp/diag_*.png` 并随 artifact 上传；本地 exe 的证据与完整子进程日志在 exe 旁 `evidence/` 目录。

`failure_stage` 全部取值、含义与处理方法见 **[docs/USER_MANUAL.md §7 错误信息速查](docs/USER_MANUAL.md#7-错误信息速查)**。

---

## 目录结构

```
xuexitong/
├── app/                      # MVP 产品层/运行时
│   ├── __init__.py
│   ├── run.py                #    入口：initialize/run/scheduler/switch/tdvp/probe
│   ├── e2_headed_gha.py      #    E2 headed-browser 引擎（10 项闭合验证，参数化）
│   ├── registry/             #    任务注册表 / reconcile / click-probe
│   │   ├── __init__.py
│   │   ├── task_registry.py  #    TaskRecord, save/load, done_chapter_ids, reconcile_queue, points
│   │   ├── reconcile.py      #    reconcile_registry, stale_completed_by_catalog, pick_conflict
│   │   └── click_probe.py    #    click_probe_chapter_id
│   └── requirements.txt
├── scheduler/                # E6: 调度决策引擎
│   ├── __init__.py
│   ├── models.py
│   └── scheduler.py          #    determine_action(), run_scheduler(), record_result()
├── tvdp/                     # E7: Task Discovery & Verification Protocol
│   ├── __init__.py
│   └── tdvp.py               #    PassiveProbe / ActiveProbe / EvidenceAggregator
├── state/                    # 持久化状态（git commit 跨 Run 保留）
│   ├── active_course.json    #    当前活跃课程 identity key
│   ├── tdvp_tasks.json       #    TDVP 任务注册表（章节→任务状态）
│   └── courses/              #    每门课程独立状态文件
│       └── <course_id>_<clazz_id>.json
├── tests/
│   ├── unit/                   #    纯函数 / 数据结构 / parser / 状态机
│   ├── integration/            #    scheduler+registry / persistence / queue
│   ├── regression/             #    （规划中）历史事故复现测试
│   └── fixtures/               #    （规划中）真实 DOM/state/历史运行快照
├── docs/
│   ├── architecture/           #    架构/状态机/scheduler/registry 说明 + E3/E5/E6/E7 实验报告
│   ├── engineering-review/     #    工程审计 / 考古 / 回归矩阵 / CI 门禁 / Agent 规则
│   ├── runbooks/               #    本地 Playwright 运行手册 / 能力矩阵 / 滑块验证码处理
│   └── evidence/               #    E2E 基线报告 / 真实登录证据 / evidence_*.json 历史快照
├── scripts/                    #    本地诊断/验证脚本 + 用户脚本
│   ├── mooc2_probe.py          #    真实（mooc2 入口）只读登录 + 目录 E2E 验证
│   └── v3_optimized.user.js    #    浏览器 end-user script
├── .github/workflows/
│   ├── run.yml               # 产品工作流（initialize/run/scheduler/switch/tdvp/probe + schedule cron）
│   ├── e2.yml                # 内部证据/验证工作流（保留）
│   └── e3.yml                # 内部可靠性工作流（保留）
└── README.md
```

---

## TDVP 内置探针（Scheduler 自动执行）

TDVP 已内置于 Scheduler，用户无需关心。每次 scheduler 运行时自动：

```
Scheduler 触发
    ↓
从 state/active_course.json 读取课程 URL
    ↓
TDVP Passive Probe（后台静默，HTML/DOM 解析）
    ↓  扫描任务列表，更新 state/tdvp_tasks.json
TaskStatus = COMPLETED / PENDING / UNKNOWN
    ↓
determine_action() → RUN / NOOP / BLOCKED
    ↓
若 RUN：从 tdvp_tasks.json 取 next_task，调用真实 Runtime
    ↓
更新 state + 推送 to main
```

**决策类型**：`RUN` / `NOOP` / `BLOCKED` / `ERROR`
**结果类型**：`SUCCESS` / `NOOP` / `BLOCKED` / `FAILED`
**并发控制**：`concurrency.group: xuexitong-active-course`（同一活跃课程同一时间只有一个执行实例）

---

## Scheduler 运行模型

```
GitHub Actions Schedule / Manual Trigger
              ↓
        Scheduler Entry
              ↓
     load_active_course()      ← reads state/active_course.json
              ↓
     load_course_state()       ← reads state/courses/<key>.json
              ↓
       determine_next_action()
         ├─ NOOP   (无活跃课程 / 无待执行工作)
         ├─ BLOCKED (连续失败 ≥3 次 / 课程被锁定)
         └─ RUN    (有工作，调用现有 Runtime)
              ↓
           Browser Runtime       ← 同一 app/run.py --action run
              ↓
           Verification
              ↓
        record_result()         ← 更新 scheduler state (consecutive_failures 等)
              ↓
           Persist (git commit + push to main)
```

---

## 本地调试（可选，需 Xvfb）

```bash
Xvfb :99 -screen 0 1440x900x24 -ac &
export DISPLAY=:99
export CX_USER=... CX_PASS=...

# Initialize（创建课程状态，仅需一次）
python app/run.py --action initialize --course-url "https://mooc1.chaoxing.com/..." --output ./evidence/result.json

# Scheduler（自动学习，无需传 course_url/chapter_id）
python app/run.py --action scheduler --trigger manual --run-id local --output ./evidence/result.json

# 直接 Run（单视频学习，需指定 chapter_id）
python app/run.py --action run --course-url "https://mooc1.chaoxing.com/..." --chapter-id 1217304706 --output ./evidence/run_<ts>.json
```

---

## 本地 exe（Windows 双击即用，可选）

不想用 GitHub Actions 的用户可把项目打包为本地常驻程序：产物**自带 chromium**，双击即刷，课程/凭据/状态全部落在 exe 旁边，与 GHA 模式共用同一套代码与状态机。

```bash
.venv/Scripts/python.exe build.py     # 产物 dist/Xuexitong/（内置浏览器，随包附带使用手册）
```

**详细操作说明、配置参考（.env / CLI 全表）、错误信息速查与 FAQ 见 [docs/USER_MANUAL.md](docs/USER_MANUAL.md)**（打包后随包附带 `dist/Xuexitong/使用手册.md`）。要点：

- 双击 `Xuexitong.exe`：首次引导填账号（写入旁 `.env`）并粘贴课程 URL；此后每次启动有**可跳过向导**（回车开始 / `s` 换课 / 数字调本次每轮章数）；
- 常驻循环：空闲轮 30 分钟、活跃轮（刚推进过任务）2 分钟，决策与熔断 cooldown 与 GHA 完全同源；课程完成自动退出，`Ctrl+C` 优雅停止；
- 升级只替换 `Xuexitong.exe` + `internal/`，保留 `state/`、`.cache/`、`.env`。

与 GHA 的错峰双跑与状态收敛详见手册 §8。核心结论：两边都以学习通服务端为真源（每轮探针重扫服务端完成标记），**错峰跑无需显式同步**；只要避开 cron 时间窗（北京 00:07 / 02:07）不重叠、换课两边各自做即可。

---

## 约束合规声明

全程仅真实浏览器自然播放；不调用/构造/伪造/重放 `multimedia/log`；不修改
`enc/attDurationEnc/videoFaceCaptureEnc/playingTime/_t`；失败如实记录
`failure_stage`，不跳过播放；同账号严格串行（避免多终端并发登入被 `detect.chaoxing.com` 判定异常）。

---

## 已知注意事项

1. **URL 参数大小写敏感**：`clazzid` 必须小写（服务端区分 `clazzid` / `clazzId`），大写会导致卡片 iframe 不渲染。
2. **openc / hidetype 必需**：缺失时服务端不渲染 knowledge/cards iframe，返回 `NO_CARDS_IFRAME`。务必从浏览器地址栏完整复制 URL。
3. **章节类型限制**：部分章节是 PDF/视频混合类型，纯视频播放路径无法达成 `isPassed=true`。如遇到 `VIDEO_NOT_COMPLETED`，请尝试其他纯视频章节。
4. **并发限制**：同一账号同时运行多个 MVP 会互相踢会话，请使用不同账号或串行执行。
5. **State 持久化**：`state/` 目录通过 git commit 跨 Run 保留。确保仓库 GITHUB_TOKEN 有 write 权限（已默认配置）。
6. **Schedule 频率**：默认每日 UTC 02:00 触发一次。如需调整，修改 `.github/workflows/run.yml` 中的 cron 表达式。
7. **TDVP Passive Probe**：依赖学习通页面 DOM 结构（`.task-item > .status` 数字标记），UI 改版可能需要调整解析正则。
8. **Task ID 格式**：任务 ID 格式为 `<chapter_id>_<section_num>`（如 `1217304702_1_3`），由 Passive Probe 从页面标题提取。
