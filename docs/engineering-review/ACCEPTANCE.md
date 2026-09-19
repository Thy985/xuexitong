# xuexitong 验收体系（ACCEPTANCE）

> Draft v1.0 · 2026-09-15
> 关联：`docs/REQUIREMENTS.md`（需求）、`docs/architecture/DEVELOPMENT_PLAN.md`（实现）。
> 本文件回答三件事：**每个里程碑"通过"长什么样、拿什么证据判、未通过怎么办**。
> 凡是"看起来能跑"都**不算**验收通过；必须以本文件定义的客观证据为准。

---

## 1. 验收的四层金字塔

> 越往上越接近真实，越往下越便宜、越快、越可自动化。任何一层不过就 **不得**进入下一层。

```
L4  上云回归（GHA cron 真跑）            —— 只验收 Step「上云」
L3  真站冒烟（真账号/真课程）             —— 本地，Playwright 起浏览器连真站
L2  功能/稳定性验证（本地，含真浏览器但可离线/可控）  —— Xvfb 有头 / headless=False
L1  纯逻辑测试（单元/集成/回归）         —— pytest，CI 已跑（现 209 passed + 1 skip）
```

### L1 · 纯逻辑测试（CI，pytest）
- **跑法**：`.github/workflows/test.yml`（push/PR 自动）→ `pytest tests/unit tests/integration tests/regression`。
- **判据**：全部通过；`--maxfail=5` 内不爆炸（不允许零星断言失败仍绿）。基线：2026-09-15 实测 **209 passed, 1 skipped**（共 210 collected）。
- **证据**：`pytest.log`（失败时自动上传 artifact）。
- **门禁**：任何 PR/M0~M3 改动必须保持 L1 全绿。**此层失败 = 阻断。**

### L2 · 本地功能/稳定性验证（真浏览器）
- **跑法**：`python scripts/ci_local_run.py --action scheduler --trigger manual`
  （M0 落地；Xvfb `:99` 或 Windows headless=False）。
- **判据**：
  - `verdict == PASS` 且 `verification_10` 10 项全 True；
  - 连续 `--max-chapters 2` 跑 3 次，同一课程进度**只前进、不重复、不卡死**；
  - 任一失败 `failure_stage` 非空且产生截图。
  - **可配浏览器（R-08，M1）**：`XUE_BROWSER_CHANNEL=msedge`（或 `XUE_BROWSER_EXE`）下 scheduler 仍能 launch 且 evidence/归因不变；未设时用默认 chromium（回归基线）。
- **证据**：`evidence/local_<ts>.json` + `diag_*.png`（如有失败）。
- **通过 = M0 达成；** L2 稳定 3/3 前，禁止进入 M3 上云。

### L3 · 真站冒烟（真实学习通）
- **跑法**：真实 `CX_USER/CX_PASS`，`scripts/mooc2_probe.py`（已有）+ 一次真实 run。
- **判据**：登录成功、抓到 `studentstudy` 目录、一个视频任务点真正推进且 registry 写 `SERVER_VERIFIED`。
- **证据**：冒烟输出 + 截图归档到 `docs/evidence/`。
- **目的**：证明"代码在连接真实的 lyse URL/DOM 时也能跑"，堵住"本地能跑但真站被改版/验证码挡住"。

### L4 · 上云回归
- **跑法**：GHA `run.yml` 定时+手动，`action: scheduler`。
- **判据**：
  1. cron 连续 3 个自然日都产出 PASS（或非 BLOCKED 且证据完整）；
  2. **本地 L2/L3 与云的 `verification_10` 序列逐项一致**（diff 为 0 差异）——即"本地行、云就行"被直接证明。
- **证据**：每个 cron run 的 evidence artifact + 一致性 diff 报告。
- **回退触发**：若云端 evidence 与本地偏差（同样输入不同 `verification_10` 结果），**回退自查本地**，不强行修云。

---

## 2. 里程碑验收矩阵（Requirements → 判据 → 证据）

> Gate 语义：**达标（可进入下一阶段）/ 未达标（修复后再验）**。

| 里程碑 | 验收项 | L 层 | 达标判据 | 证据 |
|---|---|---|---|---|
| **M0 本地基线** | R-01 本地 runbook | L2 | `ci_local_run.py` 跑通 scheduler，PASS 可复现 | `evidence/local_*.json` |
|           | R-02 环境可复现 | L1 | 新机器按 `LOCAL_FIRST_SETUP.md` 10min 内可 run | 文档 pass |
|           | R-03 诊断打包 | L2 | 故意失败能产出 `diag_*.png`+registry dump 的 zip | zip 留痕 |
| **M1 健壮自愈** | R-04 自动续播 | L2 | `paused` 后自动 recovery，`recovered_count` 记录 | evidence 字段 |
|             | R-06 静音/倍速 | L2 | 开启后 `currentTime` 正常推进、无报错 | 运行日志 |
|             | R-07 轮询降噪 | L2 | 真异常 warn/error 出现；expected 静默 debug | 日志分级 |
|             | R-08 可配浏览器 | L2 | `XUE_BROWSER_CHANNEL=msedge` 时仍能 launch 且 evidence 一致 | launch 冒烟 + evidence |
|             | R-05 滑块(可选) | L3 | 开启时滑块自动拖动成功；关闭时不触发 | 登录冒烟 |
|             | **多课时稳定性** | L3 | 长挂4h不崩、`verification_10` 稳定、无随机失败 | 4h 记录 |
| **M2 状态机** | R-10 防重复 | 1/2 | 已完成 chapter 重复 run 不 reset 进度 | registry 用例 |
|             | R-11 多课程 | L1 | `course_urls` 列表逐课程独立持久化 | 单元测试 |
|             | R-12 失败清零 | L1 | PASS 后 `consecutive_failures==0 & blocked==0` | 单元测试 |
| **M3 上云** | R-20 同构引擎 | L4 | `run.yml` 调 `ci_local_run.py`（不重写核心） | workflow diff |
|             | R-21/R22 证据一致 | L4 | cron 3日 PASS + 本地/云 `verification_10` diff=0 | 3 张 evidence |

---

## 3. 验收执行与留痕

- **执行**：每个里程碑由维护者跑对应 L 层验收；`pytest` 由 CI 自动跑。
- **留痕**：验收结果以**验收记录表**追加到本文档第 4 节（非覆盖）。
- **定版**：一个里程碑**全部子项 PASS 且证据可复现**才标记 `✅ 完成`；任一 FAIL 打回 `🔁 修改`，不空过。
- **单一事实来源**：验收结果由本文档承载，避免"口头说好了但没证据"。

---

## 4. 验收记录表（滚动追加）

| 日期 | 里程碑 | L 层 | 验收项 | 结果 | 证据文件 / 实测 |
|---|---|---|---|---|---|
| 2026-09-15 | L1 基线 | L1 | `pytest tests/unit integration regression` 全绿 | ✅ 达标 | 本地 209 passed + 1 skipped；CI run 35440057875 → **210 passed in 72s** |
| 2026-09-19 | M0 | L1 | 升 playwright 1.62.0→1.63.0 后 L1 无回归 | ✅ 达标 | 同上（本地/CI 双测）；`cfb2875` |
| 2026-09-19 | M0 | L1 | R-02 环境可复现（新机器按 `LOCAL_FIRST_SETUP.md` 可 run） | ✅ 达标 | `uv venv` + `uv pip install` 一次成功；`PROJECT-PASSPORT.md` §3 记录全流程 |
| 2026-09-19 | M1 | L2 | R-08 可配浏览器：`channel=msedge` 能 launch 且断言一致 | ✅ 达标 | chromium→`153.0.8010.12` / msedge→`153.0.4234.32`，`set_content`+`inner_text` 均命中；复用 `chromium-1243` **零下载** |
| 2026-09-19 | M0 | L2 | R-01 本地 runbook：`ci_local_run.py` scheduler 跑通且 `verdict==PASS` | 🔁 未达标 | 环境已就绪（前 3 项为其前置）；**尚未执行真站 run** —— 需授权，见 §7 |
| 2026-09-19 | M0 | L2 | R-03 诊断打包 `--collect-diagnostics` | ⏳ 待验 | 依赖上一条产生 FAIL |
| 2026-09-19 | M3 | L4 | R-20 同构引擎上云（1.63.0 在 GHA 生效） | ⏳ 待验 | `run.yml` pin 已改并推送；需一次云端 scheduler run 闭合 |

> 记录规则：追加不覆盖；结果不可复现时降级为「待验」而非删除。

---

## 5. 失败 / 回退策略

- **L1 失败**：阻断合并，必须修测试。
- **L3/L2 失败**：不进入下一层；反复失败则回退到上一层或上次 PASS 的 commit。
- **L4 云上偏差**：回退自查本地；定位"真站变了"还是"代码回归"，不直接改云。
- **进度不因回退损失**：回退不动 `state/` 与 registry（由 git 跨 Run 保留）。

---

## 6. 与现有工程体系衔接

- 单元/集成/回归用例 → `tests/`（已在 CI 自动运行）。
- 验收项逐步沉淀 → `docs/engineering-review/REGRESSION_MATRIX.md`。
- CI 门禁候选 → `docs/engineering-review/CI_GATES_CANDIDATES.md`。
- 真站冒烟 runbook → `docs/runbooks/`。

---

## 7. 待授权项（Agent 不自行执行的验收动作）

> 这些步骤本身没有技术障碍，但**副作用超出本仓库**，按 `PROJECT-PASSPORT.md` §4 必须先取得授权。

| 验收项 | 需要的动作 | 副作用 |
|---|---|---|
| M0 / R-01（L2） | `ci_local_run.py --action scheduler --trigger manual` | 对**真实课程**播放一个任务点（约 10–15 min），改服务端完成态并写 `state/`；`manual` 触发会**绕过 BLOCKED cooldown** |
| M0 / R-03（L2） | 故意造一次失败以验 `--collect-diagnostics` | 同上，且会在 registry 记一次失败 |
| M3 / R-20（L4） | `workflow_dispatch` 跑一次 `run.yml` | 云端真实学习 + `state/` 回写提交 |
| L4 一致性 diff | 启动 WSL2 Ubuntu 跑 Xvfb | Company 级服务，需 `D:\Company\requests\REQ-*` |

**当前挂起的具体问题（阻塞 M0/L2 通过）**：
`isPassed_seen` 判定的测量方式失效嫌疑 —— 引擎已用 `page.on("response")` 监到真实
`/mooc-ans/multimedia/log` 响应（`app/e2_headed_gha.py:391-394`），但只存 `url/t/status`、
**未存 body**；判定时改为对该 URL **二次 GET**（`:598-603`）。上报端点重复 GET 既属项目红线
禁止的「重放」，返回体也不保证再给 `isPassed`。run 35265696173 中 `isPassed_body=null`，
且结束时截图（`tmp/diag_at_end_*.png`）显示侧栏该节 `4.17 课后习题` 为**绿勾（已完成）**。
> ⚠️ 该截图是**单次结束态**，无法区分"本轮学的"还是"此前某次已学成"（09-17 之前该章可能已被
> 09-12~16 的 run 覆盖过）。因此这是**假阴性的强线索，不是定论** —— 定论需下面两步：
> ①改为捕获首次真实响应体；②同一次 run 内并排记录「真实响应 vs 二次 GET」两者，直接对比。
修复方向：在 response 监听处捕获**首次真实响应体**（不得重复请求）。

---

## 附：一句话版本

> **每段"能跑"都要有（证据 + 可复现的判据）才算通过**；分层从最便宜(L1 pytest) 到最贵(L4 上云) 逐层放行，未达标绝不止步，回退倒着查——本地、云统一引擎、证据 diff=0，才是"本地稳定→上云"被证明的时刻。