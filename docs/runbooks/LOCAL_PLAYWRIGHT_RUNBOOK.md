# LOCAL_PLAYWRIGHT_RUNBOOK.md
# 本地 Playwright 运行手册（实测验证，2026-09-10 snapshot）

> 内容基于**实测**（真实本地 Chromium + 真实超星站点只读探测），非拍脑袋。
> 涉及可执行命令均给出；涉及真实凭证的部分只描述方法、不外泄。
> 目的：让任何人在本地复现「本地能启动 / 能登录 / 能（受限）读课程」。

---

## 1. 环境清单（实测）

| 项 | 实测值 | 说明 |
|---|---|---|
| OS | Windows（本机） | GHA 为 ubuntu-24.04，本 runbook 偏本地 |
| Python | 3.12.14 | `python --version` |
| Playwright (pip) | 1.62.0 | `pip show playwright` |
| Chromium (PW cache) | chromium-1234 (build)，实测引擎版 `151.0.7922.34` | 位于 `%LOCALAPPDATA%\ms-playwright\` |
| Headless launch | `PLAYWRIGHT_LAUNCH_OK 151.0.7922.34` | 已实测可弹 |
| 系统 Edge | 存在 | 备用 |
| 凭证来源 | `.env`（`CX_USER`/`CX_PASS`）+ `utils/cookie_store` | 见 §3 |

---

## 2. 最小本地启动（headless 冒烟）

```bash
cd ~/project   # 仓库根
python -c "from playwright.sync_api import sync_playwright as sp; \
with sp() as p: b=p.chromium.launch(headless=True); print('launch',b.version); b.close()"
```
预期输出（实测）：`launch 151.0.7922.34`。
- 若报 `browser_type.launch: executable doesn't exist` → 先 `python -m playwright install chromium`。

### 有头模式（本地 Xvfb 或桌面）
Windows 桌面直接 `headless=False`（需要可视桌面）；Linux/Runner 用 Xvfb：
```bash
Xvfb :99 -screen 0 1440x900x24 -ac &
export DISPLAY=:99
python e2/e2_headed_gha.py --chapter-id 1217304705 --output ./evidence_e2.json --xvfb-display :99
```
> > `e2/e2_headed_gha.py` 会做**真实播放**并注册点（E2 实验目的），**不是只读**。仅当你明确要做完整 E2E 时才跑。

### 离线回归 vs 浏览器/真站 分工
- **离线 CI**（`.github/workflows/test.yml`，push/PR 自动跑 + 本地 `python -m pytest tests/`）：
  只 import playwright 模块、**不 launch 浏览器、不连真站** —— 覆盖 unit/integration/regression 纯逻辑与状态机。
- **真站/浏览器**（本 runbook）：登录 / 目录 / 播放证据 —— 手动或 `e2.yml`/`e3.yml`（`workflow_dispatch`）。

---

## 3. 登录 / 会话（实测：用**正确入口**可登录）

### 正确入口（关键）
- 学习后台用 **mooc2** 入口，不用 mooc1 studentstudy：
  `https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu?courseid=<id>&clazzid=<cid>&cpi=<cpi>&enc=<enc>&t=<t>&pageHeader=0&v=2&hideHead=0`
  `enc`/`t` 是**动态**的（会变，写死即失效）——应从真实跳转/发现当次取得。
- 用错（mooc1 `.../mooc-ans/mycourse/studentstudy`）→ 一直返回「用户未登录」766B（本 repo 早期结论由此误判）。

### 流程（来自 `utils/cookie_store.ensure_login`）
1. `load_cookies()` 读 `.cache/cookies.json` → 命中时先注入 `context.add_cookies`，再 `goto` 课程页（并以「页面无『用户未登录』」DOM 校验）。
2. cookie 无效 → `clear_cookies()` → **密码登录**：填 `#phone`/`#pwd`，点登录按钮，循环等 URL 离开登录页（期间若出现滑块按 `captcha_mode` 处理）。
3. 成功 → `save_cookies(context)` 写回 `.cache/cookies.json`。

> 已改进：`ensure_login` 在 cookie 判用与返回值处都会查「页面无『用户未登录』」（`utils/cookie_store.py::_is_login_warning`），避免 URL-only 误报。

### ✅ 实测结果（mooc2 正确入口，只读）
- 登录 `login.ok=true`，页面标题 **「计算机网络-2025级」**，HTML ≈10.6MB，含 章节×6 / 目录×4 / 视频×16，无「用户未登录 / 暂无权限」。
- **`.env` 凭据在正确入口下有效**；本次**未触发滑块**。

### ⚠️ 踩坑（为什么早期会误判登录失败）
历史上 `.cache/cookies.json`（21条，2026-09-06）读的是 mooc1 入口返回的「用户未登录」766B，被误判为「密码失效/滑块拦截」。改 **mooc2** 入口即登录成功；也正因此否定了「滑块拦截」的误判。

### 滑块验证码处理（新增功能 `utils/captcha_slider.py`）
- 若某账号登录真出滑块，`ensure_login(captcha_mode="auto")` 会自动调用：
  - 自动拖拽（无图像库，按轨道宽*约0.75估距），默认最多 3 次；
  - `captcha_mode="manual"` / `"auto_then_manual"` 可交由人工，`"skip"` 跳过；
  - `XUE_CAPTCHA_MODE` 环境变量可覆盖默认。
- 边界：geetest 可能带轨迹/时限风控，自动非 100%；失败回退人工或用有效 cookie，别无限重试。

### 当前本地真实登录结论
- Chromium 能启动；用 **mooc2** 正确入口 + `.env` 凭据可**真实登录**并读课程目录（只读）。
- 未做（只读范围）：播放视频 / 注册点 / 写生产 `state/`（需另行授权跑正式 run）。

### 登出 / 清会话
```python
from utils.cookie_store import clear_cookies
```
（删除 `.cache/cookies.json`；不会动 `state/`。）

---

## 4. 常见问题

| 症状 | 处理 |
|---|---|
| `browser.exe doesn't exist` | `python -m playwright install chromium` |
| headless 白屏/只在等 | 有的站禁 HEAD；用 `headless=False`（Windows 桌面）或 Xvfb |
| cookie 登录被判定为已登录但页面是「用户未登录」 | 已修复：`ensure_login` 现用 `_is_login_warning` 二次校验 DOM（`utils/cookie_store.py`） |
| 点了登录却一直「用户未登录」 | **大概率用错入口**：用 mooc2 `/mycourse/stu?...enc/t...`，别用 mooc1 `studentstudy`。若仍不行再查滑块/凭据 |

---

## 5. 给「本地可复现」的最小建议
1. 先跑 §2 冒烟确认浏览器（`python -m playwright install chromium` 若缺引擎）。
2. 做**真实目录读取**：`cd 仓库根` → `PYTHONPATH=. python scripts/mooc2_probe.py --out docs/evidence/mooc2_evidence`（或直接 `python scripts/mooc2_probe.py`）。
   > 该脚本已从 `docs/` 迁至 `scripts/`；内部按仓库根锚定 `.env` / `utils`。输出证据写到 `docs/evidence/mooc2_evidence/`。
3. 真登录/目录/E2E 以外的**离线回归**走 `python -m pytest tests/`（无需浏览器，见 `tests/` 布局与 `.github/workflows/test.yml`）。
4. 不要对生产 `state/` 写真实学习；证据放 `docs/evidence/` 或临时目录。
5. 真实 DOM/state/网络快照的离线 fixture 放 `tests/fixtures/`（脱敏，见 `tests/fixtures/README.md`）。