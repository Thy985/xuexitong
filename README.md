# xuexitong — 学习通自然学习 MVP

> 对超星学习通（chaoxing）课程，**Fork → 设置 Secrets → Initialize → (可选) Switch → GitHub Actions Scheduler**，
> 系统按计划自动唤醒 Runtime，基于持久化状态决定执行/跳过，输出可审计的 **Evidence**。

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

### 4. 触发 Scheduler 或 Schedule（自动学习）

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
gh workflow run run.yml \
  -f action=scheduler \
  -f course_url="https://mooc1.chaoxing.com/..." \
  -f chapter_id=1217304708
```

### 5. 切换课程（可选）

如需学习另一门课程：
- `action`: **switch**
- `course_url`：新课程 URL

系统会自动归档旧课程状态，激活新课程，后续 Scheduler 将自动跟随新课程。

---

## 产物（Evidence + 诊断）

Run 完成后，Actions 日志自动打印结构化诊断，并生成 artifact **`mvp-evidence-<run_id>`**：

| 产物 | 说明 |
|------|------|
| `evidence/result.json` | 信封（verdict/passed_count/failure_stage）+ 完整 Evidence（milient：`verification_10`、video_duration、multimedia/log count、isPassed body、nextunit） |
| `state/` | 持久化课程状态（跨 Run 有效） |
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
│   ├── run.py                #    入口：initialize/run/scheduler/switch → 自然学习
│   └── requirements.txt
├── scheduler/                # E6: 调度决策引擎（WHEN to run / WHAT to invoke）
│   ├── __init__.py
│   ├── models.py             #    SchedulerState, ExecutionResult 等类型
│   └── scheduler.py          #    determine_action(), run_scheduler(), record_result()
├── state/                    # 持久化状态（git commit 跨 Run 保留）
│   ├── active_course.json    #    当前活跃课程 identity key
│   └── courses/              #    每门课程独立状态文件
│       └── <course_id>_<clazz_id>.json
├── e2/                       # 内部验证引擎（E-series 模式，保留）
│   └── e2_headed_gha.py      # 参数化 10 项闭合验证（course-URL 通用）
├── e3/                       # 内部可靠性实验（保留）
│   ├── e3_ci_run.py
│   └── E3_Final_Report.md
├── e6/                       # E6 Evidence Pack
│   ├── E6_scheduler_report.md
│   └── evidence_e6.json
├── tests/
│   ├── test_scheduler.py     #    Scheduler 单元测试（12 cases）
│   └── ...
├── .github/workflows/
│   ├── run.yml               # 产品工作流（initialize/run/scheduler/switch + schedule cron）
│   ├── e2.yml                # 内部证据/验证工作流（保留）
│   └── e3.yml                # 内部可靠性工作流（保留）
└── README.md
```

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

**决策类型**：`RUN` / `NOOP` / `BLOCKED` / `ERROR`
**结果类型**：`SUCCESS` / `NOOP` / `BLOCKED` / `FAILED`
**并发控制**：`concurrency.group: xuexitong-active-course`（同一活跃课程同一时间只有一个执行实例）

---

## 内部 Evidence / 实验模式（保留入口）

产品引擎的原始 E-series 实验与证据入口仍在：

| 工作流 | 说明 |
|--------|------|
| `e2.yml` | 有头浏览器(Chromium+Xvfb)对指定 `chapter_id` 跑 10 项闭合验证（默认 1.6 演示课程） |
| `e3.yml` | E3 可靠性连跑（`run_label`/`chapter_id`） |

这些不变，仅供审计/复现原始实验；产品模式统一走 `run.yml`。

---

## 本地调试（可选，需 Xvfb）

```bash
Xvfb :99 -screen 0 1440x900x24 -ac &
export DISPLAY=:99
export CX_USER=... CX_PASS=...
# Initialize（创建/切换课程状态）
python app/run.py --action initialize --course-url "https://mooc1.chaoxing.com/..." --output ./evidence/result.json
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
