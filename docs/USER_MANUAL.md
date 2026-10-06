# xuexitong 使用手册

> 面向使用者的完整操作说明与故障排查。README 只保留快速开始与架构导航；
> 本文是**唯一**的详细操作文档：本地 exe 的安装配置、启动向导、常驻行为、
> 配置参考、状态文件说明、错误信息速查、报告故障与 FAQ。
>
> 边界重申：本项目只做「真实浏览器自然播放 → 服务端完成」，
> 不伪造 `multimedia/log`、不修改 `enc/attDurationEnc` 等签名参数、不跳过播放。

---

## 目录

1. [两种运行模式](#1-两种运行模式)
2. [本地 exe：打包与安装](#2-本地-exe打包与安装)
3. [本地 exe：首次配置](#3-本地-exe首次配置)
4. [启动向导与常驻行为](#4-启动向导与常驻行为)
5. [配置参考](#5-配置参考)
6. [数据与状态文件](#6-数据与状态文件)
7. [错误信息速查](#7-错误信息速查)
8. [与云端 GHA 错峰双跑](#8-与云端-gha-错峰双跑)
9. [FAQ](#9-faq)
10. [报告故障（生成诊断包）](#10-报告故障生成诊断包)

---

## 1. 两种运行模式

| | 云端 GHA（默认） | 本地 exe |
|---|---|---|
| 运行位置 | GitHub Actions 定时冷启动 | 你的 Windows 电脑常驻 |
| 凭据 | 仓库 Secrets（CX_USER/CX_PASS） | exe 旁 `.env` 或启动向导 |
| 状态存储 | 仓库 `state/`（git 提交保留） | exe 旁 `state/`（本地文件） |
| 触发 | 北京时间每天 00:07 / 02:07 各一次 | 双击启动，空闲轮 30 分钟/活跃轮 2 分钟 |
| 适合 | 挂在 fork 仓库上无人值守 | 想盯着刷、随时干预、或没有 fork 的用户 |

两种模式跑的是**同一套代码与状态机**，可错峰并用（见 [§8](#8-与云端-gha-错峰双跑)）。

---

## 2. 本地 exe：打包与安装

### 2.1 打包（开发者，Windows）

```bash
.venv/Scripts/python.exe build.py
```

产物 `dist/Xuexitong/`：

```
dist/Xuexitong/
├── Xuexitong.exe      # 主程序（双击 = 常驻刷课）
├── 使用手册.md         # 即本文档
├── .env               # 凭据模板（首次打包自动生成，填入账号密码）
├── internal/          # 运行时 + 内置 chromium（自包含，离线可用）
├── state/             # 课程状态（运行时生成）
├── evidence/          # 每章学习证据 JSON（运行时生成）
└── .cache/            # 登录 cookie（运行时生成）
```

> 国内网络 chromium 下载超时时，用镜像重试：
> `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright .venv/Scripts/python.exe build.py`
> 或给终端设代理（`HTTPS_PROXY=http://127.0.0.1:<端口>`）。

### 2.2 分发与升级

- **分发**：整个 `dist/Xuexitong/` 目录拷给别人即可，对方无需装 Python。
- **升级**：用新构建的 `Xuexitong.exe` + `internal/` 覆盖旧文件；`state/`、`.cache/`、`.env` **不要覆盖**（课程状态、登录态、凭据都在里面）。
- 杀毒软件可能对 PyInstaller 产物误报，加白名单即可（exe 与 internal 里的 node.exe）。

---

## 3. 本地 exe：首次配置

### 3.1 凭据（学习通账号）

两种方式，任选其一：

- **向导输入（推荐）**：双击 exe，首次运行会提示输入手机号与密码，写入 exe 旁 `.env`，下次不再问。
- **手工编辑**：记事本打开 `dist/Xuexitong/.env`：

  ```
  CX_USER=你的手机号
  CX_PASS=你的密码
  ```

优先级：**真实环境变量 > .env**（与 CI 语义一致）。`.env` 含明文密码，勿上传、勿入库（已在 .gitignore）。

### 3.2 课程 URL（怎么拿、每个参数是什么）

1. 浏览器登录学习通，进入目标课程的**章节学习页**（随便某一章即可）；
2. 从**地址栏完整复制** URL，形如：

   ```
   https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304708&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd...&mooc2=1&hidetype=0&openc=9b5661...
   ```

| 参数 | 含义 | 注意 |
|---|---|---|
| `chapterId` | 起始章节锚点 | 从哪章开始都行，之后自动向后推进 |
| `courseId` / `clazzid` | 课程 / 班级 | **`clazzid` 必须小写**，大写会导致页面不渲染 |
| `cpi` | 课程上下文 | 必需 |
| `enc` | 服务端签名 | 必需，整段复制 |
| `hidetype=0` / `openc=...` | 页面渲染开关 | **缺失会导致 NO_CARDS_IFRAME**（见 §7） |

校验通过后程序自动激活课程（`resolve_course` 缺参数会当场报错，按提示重新完整复制即可）。之后每轮执行都从这份 URL 重建当章地址，**不需要**每章重新复制。

---

## 4. 启动向导与常驻行为

### 4.1 每次启动的向导（可跳过）

双击 exe 后（首次配置完成的前提下）：

```
[loop] 当前课程:265997861_151695658「课程名」(已完成 27/55 章)
[loop] 每轮推进章数 max_chapters:1(输入 2/3/… 仅本次会话生效)
[loop] 回车直接开始;s=换课:
```

- **直接回车** = 按现状开始；
- **输入数字**（如 `3`）= 本次会话每轮最多推进 3 个视频任务点，不写回任何配置；
- **输入 `s`** = 换课：粘贴新课程 URL → 旧课自动归档、进度保留（换回来可继续）；
- `max_chapters` 优先级：**命令行 `--max-chapters` > `.env` 的 `XUE_LOOP_MAX_CHAPTERS` > 向导 > 默认 1**；
- 计划任务/管道等非交互场景自动跳过向导，走 `.env`/默认值。

### 4.2 常驻循环

```
每轮 = 一次 run_scheduler（与 GHA cron 同一决策引擎）
  ├─ 探测（PREPARE）：扫描目录 + 只深读队首附近 Top-K 章候选（默认 3，XUE_PROBE_TOPK 可调）；
  │   已确认无视频工作的章（视频看完/纯测验文档）自动跳过并跨轮记忆，窗口只前移不停滞
  ├─ 决策：RUN（有活）/ NOOP（没活）/ BLOCKED（熔断）/ ERROR（环境异常退避）
  └─ RUN → 逐章真实播放直到服务端 isPassed
轮间隔：活跃轮（刚推进过任务）默认 2 分钟；空闲轮默认 30 分钟
```

- **浏览器窗口**：每轮固定 2 个 Chrome 窗口——第 1 个是「备课」探测（扫描目录、
  验证候选，约 1 分钟内），关闭后第 2 个才是真正播放视频的。**看到 Chrome 打开又
  关闭是正常流程**，不是故障。
- **退出条件**：课程无可推进任务（自动退出，exit 0）；连续 5 轮 ERROR/异常（exit 1）。
- **Ctrl+C**：按一次 = 优雅停止（当前轮跑完不再开下一轮）；连按两次 = 立即退出。
- **防双开**：程序有文件锁，双开会被拒绝（同账号并发互踢会话）。
- 命令行等价：`Xuexitong.exe --action loop --interval-minutes 60 --max-chapters 3`。

### 4.3 BLOCKED 熔断与恢复

同一任务连续**内容级**失败 3 次（`consecutive_failures ≥ 3`）→ 该任务 BLOCKED；课程级熔断见 §7.3：

- **环境类失败不计入**：登录失败、会话被踢、页面框架未渲染（`LOGIN_FAILED` /
  `SESSION_KICKED` / `NO_CARDS_IFRAME` 等，见 §7.2）属于系统级故障，程序不会把它
  记到某一章头上——本轮立即终止并按 ERROR 退避（空闲轮间隔后重试，连续 5 轮自动
  退出），环境恢复后原章照常重试，**不会被跳过或冻结**；
- **自动恢复**：`schedule` 触发下每累计 4 次 BLOCKED 轮自动放行一次探测重试（`XUE_BLOCKED_RETRY_INTERVAL` 可调），恢复成功即清零；
- **立即干预**：手动触发不受 cooldown 限制——`Xuexitong.exe --action scheduler --trigger manual`；
- 具体失败原因看 `evidence/chapter_*.json` 的 `failure_stage`（见 §7.2）。

---

## 5. 配置参考

### 5.1 `.env` / 环境变量

| 键 | 默认 | 说明 |
|---|---|---|
| `CX_USER` / `CX_PASS` | 无 | 学习通账号密码（必需） |
| `XUE_LOOP_INTERVAL` | `30` | loop 空闲轮间隔（分钟） |
| `XUE_LOOP_ACTIVE_INTERVAL` | `2` | loop 活跃轮间隔（分钟，刚推进过任务后） |
| `XUE_LOOP_MAX_CHAPTERS` | `1` | loop 每轮最多推进的视频任务点数 |
| `XUE_PROBE_TOPK` | `3` | 每轮探测最多深读的候选章数（0 = 全量，旧行为） |
| `XUE_BROWSER_EXE` | 无 | 指定浏览器可执行文件（优先级最高） |
| `XUE_BROWSER_CHANNEL` | 内置 chromium | `msedge` / `chrome` 用系统浏览器（包体积小、免杀毒误报的备选） |
| `XUE_BLOCKED_RETRY_INTERVAL` | `4` | BLOCKED cooldown 自动放行的累计轮数 |
| `XUE_SCHEDULER_BUDGET_S` | `1500` | 单轮多章执行的总预算（秒） |
| `XUE_CHAPTER_MAX_S` | `900` | 单章看门狗预算（秒；长视频按实测时长自适应扩展） |

### 5.2 命令行参数（`Xuexitong.exe --help`）

| 参数 | 说明 |
|---|---|
| `--action` | `initialize` 配置课程 / `run` 单视频 / `scheduler` 单轮调度 / `switch` 换课 / `loop` 常驻（双击缺省） |
| `--course-url` | 课程 URL（initialize/switch/run 用） |
| `--chapter-id` | 指定章节（run 模式，通常不需要） |
| `--max-chapters` | 一轮最多推进 N 个视频任务点（默认 1） |
| `--interval-minutes` | loop 空闲轮间隔（分钟） |
| `--trigger manual` | 手动触发语义（不受 BLOCKED cooldown 限制） |
| `--video-index` | 章内第 N 段视频（逐段视频的章用，一般不用管） |
| `--max-attempts` | 瞬态失败重试次数（默认 2） |

> 源码形态（无 exe）：`python app/run.py <同上参数>`，行为一致；缺省 `--action run`。

---

## 6. 数据与状态文件

```
dist/Xuexitong/
├── .env                                        # 凭据（明文，勿外传）
├── .cache/
│   └── cookies-<账号哈希>.json                  # 登录态；失效才重新登录/过滑块
├── 收集故障信息.bat                              # 双击生成诊断包（见 §10）
├── VERSION                                     # 版本号（报障时请一并说明）
├── evidence/
│   ├── chapter_<id>.json                       # 每章证据（verdict/failure_stage/10 项检查）
│   ├── chapter_<id>.scheduler.stdout.log       # 子进程完整日志
│   ├── loop.log                                # 调度层日志（轮次决策/探针推进/异常）
│   └── diag_*.png                              # 失败瞬间截图
└── state/
    ├── loop.lock                               # 防双开文件锁（退出自动释放）
    └── accounts/<账号哈希>/                     # 按账号隔离
        ├── active_course.json                  # 当前活跃课程
        ├── courses/<course>_<clazz>.json       # 课程状态+进度+熔断计数
        └── registry/<course>_<clazz>/tasks.json # 任务账本（每任务状态/证据）
```

- 账号哈希 = `CX_USER` 的确定性哈希：换 `.env` 账号即切到另一套独立状态。
- **备份/迁移**：整个 `state/` + `.cache/` 拷走即可。
- **与云端对账**：把 `state/accounts/<哈希>/` 整目录拷进仓库同名路径 commit+push（或反向），两边 schema 一致（详见 §8）。

---

## 7. 错误信息速查

### 7.1 启动/配置类

| 报错 | 原因 | 处理 |
|---|---|---|
| `缺少环境变量 CX_USER / CX_PASS` | 未配置凭据 | 按 §3.1 填 `.env` 或走向导 |
| `已有另一个实例在运行（锁：…loop.lock）` | 双开 | 同账号只能跑一个；确认旧窗口已关 |
| `[!] URL 解析失败：…` | URL 不完整/参数缺失 | 从浏览器地址栏**完整**复制；注意 `clazzid` 小写、保留 `hidetype=0&openc=…` |
| `[run] 无法解析 URL，缺: [...]` | 同上 | 同上 |
| `无交互输入流，无法引导` | 计划任务/管道下缺凭据或缺课程 | 提前在 `.env` 配好；课程用 `--action initialize --course-url` |
| 双击后窗口一闪而过 | 旧版本行为（已修复） | 升级 exe；仍复现则从 cmd 运行 exe 看完整输出 |

### 7.2 运行期 `failure_stage`（evidence JSON / 调度诊断）

由引擎按固定顺序逐项自检推导（`app/e2_headed_gha.py::_derive_failure_stage`），
是定位问题的总开关——先看它，再看 `evidence.checks` 里具体哪一项为 false。

**环境类 vs 内容类**：标 🖥 的属于环境/会话级故障（登录态、页面框架、网络、崩溃），
不计入章节失败次数、**不会导致任何一章被跳过或冻结**；本轮自动终止退避，环境恢复
后原章照常重试。其余为内容/播放级，连续 3 次才 BLOCKED（§4.3）。

| failure_stage | 含义 | 常见处理 |
|---|---|---|
| `LOGIN_FAILED` 🖥 | 账号密码错误或登录被风控拦截 | 核对 `.env`；确认账号可用 |
| `SESSION_KICKED` 🖥 | 会话被踢（别处登录/多开/服务端挤下线） | 关掉其他登录终端；不要双开 exe |
| `HEARTBEAT_DEAD` 🖥 | 播放中心跳长时间停止（网络/会话断） | 检查网络；重跑恢复 |
| `STUDENTSTUDY_NOT_LOADED` 🖥 | 学习页没打开 | 检查 URL 是否过期（重新复制） |
| `NO_CARDS_IFRAME` 🖥 | 章节卡片 iframe 未渲染 | URL 缺 `openc`/`hidetype`（§3.2） |
| `NO_VIDEO_IN_CARDS` 🖥 | 有卡片但无视频子 iframe | 多为渲染抖动会自动重试；持续出现换章试试 |
| `VIDEO_METADATA_NOT_READY` 🖥 | 有界重载后仍拿不到视频时长 | 网络慢 / CDN 抖动 / 被杀毒拦截，重试 |
| `NO_MULTIMEDIA_LOG` 🖥 | 服务端心跳（multimedia/log）未上报 | 多为网络瞬态或请求被拦，重试 |
| `VIDEO_DURATION_INVALID` | 视频 duration=0 | 瞬态，重试；持续出现换章试试 |
| `PLAYBACK_NOT_STARTED` | 视频未起播 | 网络慢 / CDN 抖动 / 被杀毒拦截，重试 |
| `PLAYBACK_STALLED` | 起播后 currentTime 不增长 | 网络中断或页面被切后台导致节流，重试 |
| `VIDEO_NOT_COMPLETED` | 播到中途停止（未到 90% 时长） | 看门狗/网络；重试 |
| `ISPASSED_FALSE` | 播完了但服务端未判通过 | 视频尾部可能有确认交互，或学习通侧未落库 |
| `NO_NEXTUNIT_NO_ENDED` | 既没触发下一节也没检测到结束 | 按卡播处理 |
| `TARGET_NOT_ON_PAGE` | 账本要求的视频段在页面上不存在（账本错，非播放失败） | 程序自动纠正账本，不计失败 |
| `UNREPORTED_BY_RUNTIME` | 运行时被看门狗杀掉，未自报阶段 | 看 `result.crash` 字段与 `evidence/loop.log` 尾部 |
| `UNKNOWN` | 程序未能归类 | 人工看 `checks` 与 `key_console` |

> `NEXTUNIT_EARLY_TRIGGER` 不是失败阶段，而是引擎自纠正的记录项（翻页早于播完
> 时记证据并纠正，不计失败次数）。

### 7.3 调度状态类

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志出现 `BLOCKED` / 课程不推进 | 连续内容级失败 ≥3 熔断 | 见 §4.3：等 cooldown 自动复位，或 `--trigger manual` 立即重试 |
| `⛔ 环境类失败 …本轮终止` | 登录/会话/页面框架级故障（环境类，不计章节失败） | 看 `failure_stage`（§7.2 🖥 行）排除双开/网络/账号问题；30 分钟后自动重试，连续 5 轮 ERROR 会自动退出 |
| `verdict=PASS` 但 `result=FAILED` | 旧版本看门狗误杀的误标（已修复） | 升级 exe；任务实际已完成，不会重刷 |
| 轮次一直 NOOP | 课程完成 / 无活跃课程 / 探针无 pending | 看向导展示的进度；确认课程选对 |
| Chrome 反复打开又关闭 | 每轮固定 2 个窗口：探测（备课）+ 播放（§4.2） | 正常流程；探测窗口约 1 分钟内让位给播放窗口 |
| 探测偏慢（`[prepare] 深读候选 i/K`） | 每轮深读 Top-K 候选章（默认 3）；已确认无视频工作的章会自动跳过并跨轮记忆 | 正常，约 1 分钟内；可 `.env` 调 `XUE_PROBE_TOPK` |

### 7.4 环境/系统类

| 现象 | 处理 |
|---|---|
| 浏览器起不来 / 被杀毒拦截 | 加白名单；或 `.env` 设 `XUE_BROWSER_CHANNEL=msedge` 用系统 Edge |
| 打包时 chromium 下载超时 | 见 §2.1 的镜像/代理命令 |
| 控制台中文乱码 | 程序已强制 UTF-8；仍乱码换 Windows Terminal |
| 同账号两边跑互踢 | 错峰（§8） |

---

## 8. 与云端 GHA 错峰双跑

本地 exe 与 GHA 各有一份独立 state（本地在 exe 旁，GHA 在仓库 git 里），但两边都以**学习通服务端为真源**：每轮 TDVP 探针重新扫描服务端完成标记，章级判定只认服务端 `isPassed`。因此**错峰双跑不需要显式同步**——一边刷完的章，另一边下一轮探针自动跳过。

注意事项：

1. **时间窗错开，不能重叠**：同账号并发互踢会话。GHA cron 为北京 00:07 / 02:07，本地避开即可；长期只用本地时建议禁用 fork 仓库的 schedule。
2. **换课两边各自做**：`active_course.json` 各存各的。
3. **熔断/任务冻结不共享**：一边 BLOCKED 另一边照常重试，相当于多一条独立重试腿。
4. **进度展示计数可能两边暂时不一致**：外观问题，真相在服务端。
5. **手动对账（可选）**：`state/accounts/<哈希>/` 整目录在 exe 旁与仓库间互拷 + commit/push 即可，schema 完全一致。

---

## 9. FAQ

**Q: 刷课速度由什么决定？**
真实播放时长（一集 25 分钟就看 25 分钟）+ 每轮探测开销（约 1 分钟，有预算上限）。程序不会倍速、不会跳播——这是设计边界，也是合规底线。想连续刷就保持活跃轮短间隔（默认已是 2 分钟）。

**Q: 为什么 Chrome 会先打开又关闭，过一会才开始播？**
每轮调度先用第 1 个窗口「备课」——扫描目录、验证候选章（约 1 分钟），关掉后第 2 个窗口才是真正播放视频的（§4.2）。备课范围有预算上限（`XUE_PROBE_TOPK`，默认 3 章），不会像旧版本那样把整门课翻一遍再播。

**Q: 看完一集为什么不立刻下一集？**
活跃轮默认等 2 分钟再探测/执行（`XUE_LOOP_ACTIVE_INTERVAL` 可调）。想单轮连播多集用 `--max-chapters N` 或向导输入 N。

**Q: 中途退出会丢进度吗？**
不会。状态每轮落盘；单章是否完成以服务端 `isPassed` 为准，重启后从账本继续。

**Q: 能同时登学习通网页/手机端吗？**
可以，但运行期间同账号新终端登录可能挤掉 exe 的会话（`LOGIN_FAILED`）；重跑即可恢复。

**Q: 会被检测吗？**
本项目仅真实浏览器自然播放，不构造/重放心跳、不改签名参数、不跳播；失败如实上报。请自行评估并遵守所在学校的规则。

**Q: exe 升级后要重新配置吗？**
不用。只替换 `Xuexitong.exe` 与 `internal/`，保留 `.env`、`state/`、`.cache/`。

---

## 10. 报告故障（生成诊断包）

遇到播放失败、卡住、闪退等问题时，**不需要你描述现象**——双击 exe 目录下的
**`收集故障信息.bat`**，它会在同目录生成 `故障信息-<时间戳>.zip`，把这个 zip
作为附件发到 issue 即可。维护者能从里面直接定位到失败阶段。

**它收集什么**

| 内容 | 位置 | 作用 |
|---|---|---|
| 每章证据 | `evidence/chapter_*.json` | `failure_stage` 失败阶段 + `checks` 十项自检 |
| 子进程日志 | `evidence/chapter_*.scheduler.stdout.log` | 该章播放全过程 |
| 调度层日志 | `evidence/loop.log` | 每轮决策、探针推进、为什么没推进 |
| 失败截图 | `evidence/diag_*.png` | 失败瞬间的页面 |
| 任务账本 | `state/**/tasks.json` | 哪一章失败几次、是否熔断 |
| 环境信息 | `收集信息.txt` | 版本号、系统、收集清单 |

**它不会收集什么**（已在脚本里硬性排除）

- `.env`——你的账号密码
- `.cache/`——登录 Cookie
- `internal/`——560MB 运行时，与排障无关

此外所有文本里的**手机号、课程 URL 的 `enc` 访问令牌、CDN 的 `ak_`/`at_`/`ad_`
临时签名**都会在打包前自动打码。截图有配额（最近 5 张 / 12MB 内），避免超出
issue 附件 25MB 上传限制。

**手动等价操作**（不想用 .bat 时）

```bash
# 只要 evidence 和 state 两个目录压成 zip 也行
# 注意：不要包含 .env 与 .cache/
```

**为什么不用 exe 的 `--action` 来收集？**
因为「双击闪退」本身就是要报的问题——收集入口若挂在 exe 上，那类故障恰好
收不到任何现场。`.bat` 走 PowerShell，不依赖 exe 能运行。

**小技巧**：issue 里补一句你 `VERSION` 文件里的版本号，能省掉一轮"是不是
版本不一致"的来回。
