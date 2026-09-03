# xuexitong — E2: GitHub Actions Headed Browser Compatibility

验证在 GitHub Actions CI runner 上，以"有头浏览器 + Xvfb 虚拟显示"方式复现本地 E1.2 已 PASS 的超星 MOOC 浏览器自动化链路。

## 实验原则

- 业务逻辑与 E1.2 完全一致
- 只改变运行环境（本地 Windows Edge → GitHub Actions Ubuntu 24.04 + Chromium headed）
- 不改变任何业务参数
- 不直接调用 multimedia/log
- 不伪造播放进度
- 不重放请求
- 不修改 enc / attDurationEnc / otherInfo / _t 等参数

## 10 项验证检查点

1. 登录成功
2. studentstudy 加载
3. cards iframe 加载
4. 递归进入 ananas video iframe
5. video.duration 正常取得
6. 真实播放按钮 click（由 v3 脚本自动触发）
7. currentTime 持续增长
8. 自然产生 multimedia/log
9. 服务端 isPassed=true
10. 完成状态独立复核（banner / sidebar 变化）

## 触发方式

```bash
# 手动触发（使用默认 1.6 章节）
gh workflow run e2.yml

# 指定章节
gh workflow run e2.yml -f chapter_id=1217304705
```

## Secrets 要求

| Secret | 说明 |
|--------|------|
| `CX_USER` | 超星账号（手机号） |
| `CX_PASS` | 超星密码 |

设置方式：
```bash
gh secret set CX_USER -b "18605440838"
gh secret set CX_PASS -b "你的密码"
```

## Evidence Pack

Workflow 完成后，从 Actions 页面下载 artifact `e2-evidence-{run_number}`，包含：
- `evidence_e2.json` — 完整 Evidence（CI 环境信息、browser/version、headed/headless 状态、iframe tree、video duration、playback timeline、multimedia/log count、isPassed、post-run verification、failure stage）
- `e2/e2_headed_gha.py` — 测试脚本源码

## 本地调试（可选）

```bash
# 需要 Xvfb 已安装并运行
Xvfb :99 -screen 0 1440x900x24 -ac &
export DISPLAY=:99
CX_USER=xxx CX_PASS=xxx python e2/e2_headed_gha.py --chapter-id 1217304705 --output /tmp/evidence_e2_local.json
```