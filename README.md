# xuexitong — 学习通自然学习 MVP

> 对超星学习通（chaoxing）课程，**Fork → 设置 Secrets → Initialize → Scheduler / TDVP 探针 → GitHub Actions 定时**，
> 系统按计划自动唤醒 Runtime，基于持久化状态和任务队列决定执行/跳过，输出可审计的 **Evidence**。

**重要边界**：本项目仅做"真实浏览器自然播放 → 服务端完成"，**不**构造/伪造/重放
`multimedia/log`、不修改 `enc/attDurationEnc/videoFaceCaptureEnc/playingTime/_t`、
不跳过播放、不宣称"整门课程自动化完成"。一次 Run 只自然完成 URL 中指定的一个视频任务点
（`--max-chapters` 可设为 N 跨章节推进，但默认 1）。

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

### 4. TDVP 探针（可选，扫描课程任务列表）

**Passive Probe（纯 HTML 解析，秒级完成）**：
- `action`: **tdvp**
- `course_url`：课程 URL（任意章节页面均可）
- `chapter_id`（可选）：指定章节，缺省扫描全部可见章节

Passive Probe 从 studentstudy 页面提取所有章节的任务点，根据 UI 数字标记（`1`=完成，`0`=未完成）生成 `state/tdvp_tasks.json` 任务注册表，并同步课程进度到 `state/courses/<key>.json`。

**结果示例**：
```
Total: 8, Completed: 4, Pending: 4, Unknown: 0
  [COMPLETED] 1.1 互联网概述
  [PENDING]   1.3 计算机网络的概念与类别   ← 待验证
  [PENDING]   1.6 计算机网络的体系结构     ← 待验证
  ...
next_task: 1217304702_1_3
```

**Active Probe（对 PENDING 任务调用真实 Runtime）**：
- `action`: **probe**
- `course_url` + `task_id`（任务点 ID）

Active Probe 只针对 pending/unknown 任务执行，确认服务端 `isPassed=true`，将 confidence 升级为 `SERVER_VERIFIED`。

### 5. Scheduler 或 Schedule（自动学习）

**手动触发（一次）**：
- `action`: **scheduler**
- `course_url`：与 Initialize 相同的 URL
- `chapter_id`（可选）：缺省取 URL 里的 `chapterId`

**自动定时（每天 UTC 02:00）**：无需手动操作，Workflow 内置 `schedule` trigger。

Scheduler 会：
1. 读取 `state/active_course.json` 获取当前活跃课程
2. 读取课程状态决定本次是否执行（RUN / NOOP / BLOCKED）
3. 若 RUN，调用现有 E1/E2/E3 浏览器 Runtime 执行学习
4. 更新并持久化 state 到 main 分支

**命令行触发**：
```bash
# TDVP Passive Probe（扫描任务列表）
gh workflow run run.yml \
  -f action=tdvp \
  -f course_url="https://mooc1.chaoxing.com/..."

# Scheduler（执行一次学习）
gh workflow run run.yml \
  -f action=scheduler \
  -f course_url="https://mooc1.chaoxing.com/..." \
  -f chapter_id=1217304708

# Active Probe（验证单个任务）
gh workflow run run.yml \
  -f action=probe \
  -f course_url="https://mooc1.chaoxing.com/..." \
  -f task_id=1217304702_1_3
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

**TDVP 输出字段**：
```json
{
  "action": "tdvp",
  "course_key": "265997861_151695658",
  "total_tasks": 8,
  "completed": 4,
  "pending": 4,
  "unknown": 0,
  "task_queue": ["1217304702_1_3", "1217304702_1_6", ...],
  "next_task": "1217304702_1_3"
}
```

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
├── app/                      # MVP 产品层
│   ├── run.py                #    入口：initialize/run/scheduler/switch/tdvp/probe
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
├── e2/                       # 内部验证引擎（E-series 模式，保留）
│   └── e2_headed_gha.py      # 参数化 10 项闭合验证
├── e3/                       # 内部可靠性实验（保留）
│   ├── e3_ci_run.py
│   └── E3_Final_Report.md
├── e6/                       # E6 Evidence Pack
│   ├── E6_scheduler_report.md
│   └── evidence_e6.json
├── e7/                       # E7 Evidence Pack
│   ├── E7_tdvp_report.md
│   └── evidence_e7.json
├── tests/
│   ├── test_scheduler.py     #    Scheduler 单元测试（12 cases）
│   ├── test_tdvp.py          #    TDVP 单元测试（16 cases）
│   └── test_tdvp_integration.py
├── .github/workflows/
│   ├── run.yml               # 产品工作流（initialize/run/scheduler/switch/tdvp/probe + schedule cron）
│   ├── e2.yml                # 内部证据/验证工作流（保留）
│   └── e3.yml                # 内部可靠性工作流（保留）
└── README.md
```

---

## TDVP 两阶段探针协议

```
Course URL
    ↓
Passive Probe（HTML/DOM 解析，不启动浏览器）
    ↓  识别所有章节任务点的 UI 完成标记
TaskStatus = COMPLETED / PENDING / UNKNOWN
    ↓
Evidence Aggregator
    ├── COMPLETED(UI)  → weak evidence，无需验证
    ├── PENDING(UI)    → 需 Active Probe
    └── UNKNOWN        → 需 Active Probe
    ↓
Active Probe（仅对 PENDING/UNKNOWN，调用真实 Runtime）
    ↓  确认 isPassed=true
TaskStatus = COMPLETED(SERVER_VERIFIED) / PENDING / UNKNOWN
    ↓
Canonical Task State
    ↓
Persistent State（state/tdvp_tasks.json + state/courses/*.json）
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

# Initialize（创建课程状态）
python app/run.py --action initialize --course-url "https://mooc1.chaoxing.com/..." --output ./evidence/result.json

# TDVP Passive Probe（扫描任务列表，纯 HTML 解析）
python app/run.py --action tdvp --course-url "https://mooc1.chaoxing.com/..." --output ./evidence/result.json

# Active Probe（验证单个 pending 任务）
python app/run.py --action probe --course-url "https://mooc1.chaoxing.com/..." --task-id 1217304702_1_3 --output ./evidence/probe_result.json

# Scheduler（决定并执行学习）
python app/run.py --action scheduler --course-url "https://mooc1.chaoxing.com/..." --chapter-id 1217304706 --trigger manual --run-id local --output ./evidence/result.json

# 直接 Run（单视频学习）
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
