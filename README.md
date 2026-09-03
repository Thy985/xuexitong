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
  iframe，引擎会如实上报 `FAIL(video metadata not ready)`）。例如：
  ```
  https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a
  ```
- `chapter_id`（可选）：缺省取 URL 里的 `chapterId`。
- `max_chapters`（可选，默认 `1`）：本次最多自然完成的视频任务点数量。

命令行触发等效：

```bash
gh workflow run run.yml \
  -f course_url="https://mooc1.chaoxing.com/..." \
  -f chapter_id=1217304705 \
  -f max_chapters=1
```

### 产物（Evidence）

Run 完成后，在 Actions run 页下载 artifact **`mvp-evidence-<run_id>`**：
- `evidence/run_<ts>.json` — 信封（verdict/passed_count/failure_stage）+ 完整 Evidence
  （milient：`verification_10`、video_duration、multimedia/log count、isPassed body、nextunit）
- `app/` — 产品代码

verdict：`PASS`（10/10）｜`DEGRADED`（≥6/10，可复核）｜`FAIL`。

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
│   ├── run.yml               # 产品工作流（course_url 输入）
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