# E2E_BASELINE_001 — mooc2 正确入口真实登录 + 目录读取 基线（可复现）

- **基线号**：001
- **日期（UTC）**：2026-09-10
- **环境**：Windows 本机 · Python 3.12.14 · Playwright 1.62.0 · Chromium 151.0.7922.34（headless）
- **类型**：真实站点只读 E2E（**不播放、不注册点、不写生产 `state/`**）
- **依据**：`docs/evidence/mooc2_evidence/probe.json` + `scripts/mooc2_probe.py`（复现命令）
- **对应报告**：`docs/evidence/E2E_BASELINE_REPORT.md`（更早错入口对照）

---

## 1. 入口与账号
- **入口（正确，必用）**：`https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu?courseid=265997861&clazzid=151695658&cpi=506830460&enc=<当次>&t=<当次>&pageHeader=0&v=2&hideHead=0`
- **凭据**：`.env` 的 `CX_USER` / `CX_PASS`（有效；`enc/t` 为每次动态，需当次取得，勿写死）
- 历史坑：用 `mooc1/.../studentstudy` 会持续「用户未登录」——即登录阻挡真因是**入口用错**，非密码/滑块。

## 2. 结果（实测）
| 项 | 值 |
|---|---|
| `login.ok` | `true` |
| 最终 URL | mooc2 课程页（未回 passport 登录页） |
| 页面标题 | **计算机网络-2025级** |
| HTML | 10,650,872 B |
| 目录/章节/视频标记 | 章节×6 · 目录×4 · 视频×16 |
| full-page 截图 | `docs/evidence/mooc2_evidence/page.png` ✓ |
| `not_logged_in` / `permission_gate` | `false` |
| `personal_space` / `catalog_chapter` | `true` / `true` |

## 3. 判定
- **判定**：`PASS`（登录 + 真实课程渲染可达，只读）。
- **约束遵守**：未播放视频、未注册任何点、未写生产 `state/`。
- **可复现**：`cd 仓库根 && PYTHONPATH=. python scripts/mooc2_probe.py`
  （输出到 `docs/evidence/mooc2_evidence/`）。

## 4. 与离线体系的关系
- 本次真实 E2E 中可沉淀的**脱敏快照**已放入 `tests/fixtures/`（`dom/`、`state/`、`net/`）。
- 相应**离线回归**（DOM 漂移 / 完成判据 / 状态会计）将由 `tests/regression/` 承接，并由 `.github/workflows/test.yml` 在 push/PR 自动执行。

## 5. 变更
`Business Logic Changed: NO`（仅验证 + 记录 + fixtures 抽取 + CI `test.yml`）。