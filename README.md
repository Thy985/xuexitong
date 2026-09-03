# xuexitong — 学习通自然学习 MVP

> 对超星学习通（chaoxing）课程，**Fork → 设置 Secrets → 填写课程 URL → GitHub Actions Run**，
> 用真实有头浏览器自然播放一个视频任务点直至服务端 `isPassed=true`，输出可审计的 **Evidence**。

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

### 3. 触发 Run（输入课程 URL）

仓库 → **Actions** → 选中 `run (MVP - learn one video task naturally)` → **Run workflow**：

- `course_url`（必填）：学习通**章节 studentstudy URL**，需含
  `chapterId` / `courseId` / `clazzid` / `cpi` / `enc`。**请从浏览器地址栏直接复制**
  （务必保留末尾的 `hidetype=0&openc=...`——缺失时服务端不渲染视频
  iframe，引擎会如实上报 `FAIL(no_cards_frame)`）。例如：
  ```
  https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304708&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a
  ```
- `chapter_id`（可选）：缺省取 URL 里的 `chapterId`。
- `max_chapters`（可选，默认 `1`）：本次最多自然完成的视频任务点数量。

命令行触发等效：

```bash
gh workflow run run.yml \
  -f course_url="https://mooc1.chaoxing.com/..." \
  -f chapter_id=1217304708 \
  -f max_chapters=1
```

### 产物（Evidence + 失败诊断）

Run 完成后，Actions 日志会自动打印诊断信息（见下方"失败诊断"），并生成 artifact **`mvp-evidence-<run_id>`**：
- `evidence/run_<ts>.json` — 信封（verdict/passed_count/failure_stage）+ 完整 Evidence
  （milient：`verification_10`、video_duration、multimedia/log count、isPassed body、nextunit）
- `app/` — 产品代码
- 失败时还会上传 `/tmp/diag_*.png` 截图 + console tail

verdict：`PASS`（10/10）｜`DEGRADED`（≥6/10，可复核）｜`FAIL`。

---

## 失败诊断（不靠猜）

MVP 在每次失败时自动给出结构化诊断，直接打印到 Actions 日志：

```
══════════ FAILURE DIAGNOSTICS ══════════
verdict:        DEGRADED
failure_stage:  PLAYBACK_STALLED
passed_count:   8/10
retry_count:    0
timing_s:       102.4
crash:          none

── failed checks ──
  ✗ 9_isPassed_true
  ✗ 10_post_verification

── diagnostics ──
  at_end_video_state: {currentTime: 0.273, duration: 655.978, ...}
  at_end_page_url: https://mooc1.chaoxing.com/...
  at_end_console_tail: [[error] video load timeout, ...]
══════════════════════════════════════
```

`failure_stage` 的取值含义：

| failure_stage | 含义 |
|---|---|
| `LOGIN_FAILED` | 账号密码错误或会话被踢 |
| `STUDENTSTUDY_NOT_LOADED` | 无法打开学习页面（URL 参数可能有问题） |
| `NO_CARDS_IFRAME` | cards iframe 未渲染（检查 URL 是否含 `openc`/`hidetype`） |
| `NO_VIDEO_IN_CARDS` | cards iframe 存在但无视频子 iframe |
| `VIDEO_DURATION_INVALID` | 视频 duration=0 或异常 |
| `PLAYBACK_NOT_STARTED` | 视频加载但未起播 |
| `PLAYBACK_STALLED` | 视频起播后 currentTime 不增长（可能被 nextUnit 误判提前切走） |
| `VIDEO_NOT_COMPLETED` | 视频播放中途停止（max_ct 远小于 duration） |
| `NEXTUNIT_EARLY_TRIGGER` | nextUnit 在视频未完时被触发（章节类型不匹配/PDF 混合） |
| `ML_LOG_MISSING` | multimedia/log 未被调用（v3 脚本未注入或页面类型不支持） |

所有失败场景都会自动保存失败时截图到 `/tmp/diag_*.png`，并随 artifact 上传。

---

## 目录结构

```
xuexitong/
├── app/                      # MVP 产品层
│   ├── run.py                #    入口：--course-url → 自然学习一个视频任务点 → Evidence
│   └── requirements.txt
├── e2/                       # 内部验证引擎（Evidence/E-series 模式，保留）
│   └── e2_headed_gha.py      # 参数化 10 项闭合验证（course-URL 通用）
├── e3/                       # 内部可靠性实验（保留）
│   ├── e3_ci_run.py
│   └── E3_Final_Report.md
├── xuexitongScript/          # v3 用户脚本（播放器驱动 / nextUnit）
├── .github/workflows/
│   ├── run.yml               # 产品工作流（course_url 输入 + if:always() 诊断）
│   ├── e2.yml                # 内部证据/验证工作流（保留）
│   └── e3.yml                # 内部可靠性工作流（保留）
└── README.md
```

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
python app/run.py --course-url "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=...&courseId=...&clazzid=...&cpi=...&enc=..." --output ./evidence/run_<ts>.json
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
