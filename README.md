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

### 3. Initialize（创建课程状态）

仓库 → **Actions** → 选中 `run` → **Run workflow**：

- `action`: **initialize**
- `course_url`（必填）：学习通**章节 studentstudy URL**，需含
  `chapterId` / `courseId` / `clazzid` / `cpi` / `enc`。**请从浏览器地址栏直接复制**
  （务必保留末尾的 `hidetype=0&openc=...`——缺失时服务端不渲染视频 iframe，引擎会如实上报 `FAIL(no_cards_frame)`）。例如：
  ```
  https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304708&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a
  ```

初始化完成后，`state/active_course.json` 和 `state/courses/<course_id>_<clazz_id>.json` 会自动提交到 main 分支。

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
2. **TDVP Passive Probe**（后台静默）：扫描任务列表，更新 `state/tdvp_tasks.json`
3. 读取课程状态决定本次是否执行（RUN / NOOP / BLOCKED）
4. 若 RUN，自动选择下一个 pending 任务，调用浏览器 Runtime 执行学习
5. 更新并持久化 state 到 main 分支

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

MVP 在每次失败时自动给出结构化诊断，直接打印到 Actions 日志：

```
══════════ SCHEDULER DIAGNOSTICS ══════════
action:          scheduler
decision:        RUN
result:          SUCCESS
trigger:         manual
course_key:      265997861_151695658
verdict:         PASS
timing_s:        792.5
error:           null
```

`failure_stage` 取值含义：

| failure_stage | 含义 |
|---|---|
| `LOGIN_FAILED` | 账号密码错误或会话被踢 |
| `STUDENTSTUDY_NOT_LOADED` | 无法打开学习页面（URL 参数可能有问题） |
| `NO_CARDS_IFRAME` | cards iframe 未渲染（检查 URL 是否含 `openc`/`hidetype`） |
| `NO_VIDEO_IN_CARDS` | cards iframe 存在但无视频子 iframe |
| `VIDEO_DURATION_INVALID` | 视频 duration=0 或异常 |
| `PLAYBACK_NOT_STARTED` | 视频加载但未起播 |
| `PLAYBACK_STALLED` | 视频起播后 currentTime 不增长 |
| `VIDEO_NOT_COMPLETED` | 视频播放中途停止 |
| `NEXTUNIT_EARLY_TRIGGER` | nextUnit 在视频未完时被触发 |
| `ML_LOG_MISSING` | multimedia/log 未被调用 |

所有失败场景都会自动保存失败时截图到 `/tmp/diag_*.png`，并随 artifact 上传。

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
├── e7/                       # E7 Evidence Pack（历史报告/证据）
│   ├── E7_tdvp_report.md
│   └── evidence_e7.json
├── tests/
│   ├── unit/                   #    纯函数 / 数据结构 / parser / 状态机
│   ├── integration/            #    scheduler+registry / persistence / queue
│   ├── regression/             #    （规划中）历史事故复现测试
│   └── fixtures/               #    （规划中）真实 DOM/state/历史运行快照
├── docs/
│   ├── architecture/           #    架构 / 状态机 / scheduler / registry / persistence 说明
│   ├── engineering-review/     #    工程审计 / 考古 / 回归矩阵 / CI 门禁 / Agent 规则
│   ├── runbooks/               #    本地 Playwright 运行手册 / 能力矩阵 / 滑块验证码处理
│   └── evidence/               #    E2E 基线报告 / 真实登录证据 / 历史日志
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
