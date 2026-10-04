"""diag_summary — 把散落的运行产物汇总成一份人可直接读的定位结论。

给谁用：提 issue 的人通常不会描述问题（只会说"播放失败"），所以压缩包必须
能替他说话。维护者拿到包后第一眼要看到的不是几百行 JSON，而是「哪一章、
卡在哪一步、大概率什么原因」——这正是本模块的产出。

汇总三份真源（运行时就已落盘，不依赖任何额外开关）：
  1. evidence/chapter_*.json  的 result.failure_stage + evidence.checks
     —— 程序自己判定的失败阶段，定位的总开关
  2. state/**/registry/*/tasks.json 的 failure.stage / consecutive_failures
     —— 账本视角：哪一章被熔断、失败几次
  3. evidence/loop.log 的尾部 —— 调度层为什么没推进（选错章/NOOP/熔断/探针空转）

判读只陈述观测、不猜修复：给出「现象 → 最可能原因」，处置留给维护者。
本模块纯只读，不参与任何刷课决策。

命令行（收到用户交来的 zip 后解压即可跑，产物是一份 Markdown 摘要）：

    python -m utils.diag_summary D:/tmp/故障信息-20261004-193855

    # 指定 loop.log 取的尾部行数、写到文件而不是标准输出
    python -m utils.diag_summary <目录> --loop-log-lines 120 -o 摘要.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.version import app_version, build_info, git_sha

# ── failure_stage → (现象, 最可能原因) ────────────────────────────────
# 与 app/e2_headed_gha._derive_failure_stage() 的判定顺序严格对应。
STAGE_MEANING: "dict[str, tuple[str, str]]" = {
    "LOGIN_FAILED": ("账号密码错误，或同账号在别处登录把会话挤掉了",
                     "核对 .env 的 CX_USER/CX_PASS；确认没有其它设备/浏览器同时在线"),
    "STUDENTSTUDY_NOT_LOADED": ("学习页没打开（URL 失效或被重定向）",
                     "课程 URL 有时效，重新从浏览器地址栏完整复制一次"),
    "NO_CARDS_IFRAME": ("章节卡片 iframe 未渲染",
                     "URL 缺 openc / hidetype 参数，复制时被截断"),
    "NO_VIDEO_IN_CARDS": ("该章节卡片里没有视频（可能是文档/测验类章节）",
                     "属正常跳过，程序会自动换下一个任务"),
    "VIDEO_DURATION_INVALID": ("视频 duration 读成 0，播放器未就绪",
                     "CDN/网络瞬态，重试通常可恢复"),
    "PLAYBACK_NOT_STARTED": ("视频未起播",
                     "网络慢、视频 CDN 被墙/被杀毒拦截，或播放器被策略阻止"),
    "PLAYBACK_STALLED": ("起播了但 currentTime 不增长（卡播）",
                     "网络中断或页面被切到后台导致节流"),
    "NO_MULTIMEDIA_LOG": ("服务端心跳（multimedia/log）未上报",
                     "网络瞬态或请求被拦，重试观察"),
    "VIDEO_NOT_COMPLETED": ("播到中途就停了，未到 90% 时长",
                     "网络抖动或被看门狗超时收割"),
    "ISPASSED_FALSE": ("播完了但服务端未判通过",
                     "视频尾部可能有确认交互，或学习通侧判定未落库"),
    "NEXTUNIT_EARLY_TRIGGER": ("翻到下一小节早于播完",
                     "页面自动切换，程序会自行纠正，记证据即可"),
    "NO_NEXTUNIT_NO_ENDED": ("既没触发下一节也没检测到播放结束",
                     "播放停滞，按卡播处理"),
    "UNREPORTED_BY_RUNTIME": ("运行时崩溃或被看门狗杀掉，未自报失败阶段",
                     "看 result.crash 与 loop.log 尾部"),
    "UNKNOWN": ("程序未能归类", "需人工看 checks 与 console 日志"),
}

# checks 里为 False 即代表这一环没过，按引擎判定顺序排列（诊断时最先看它）
CHECK_ORDER = [
    "login_ok", "studentstudy_loaded", "cards_iframe_loaded", "cards_has_video",
    "video_duration_ok", "playback_started", "isPassed_seen", "ended_seen",
]

# 手机号：11 位、前 3 后 4 保留、中间 4 位打码。
# 两处易错：① `(?<!\d)`/`(?!\d)` 必须都在，否则会从 13 位时间戳/objectid 的
# 中间截出一段假号码；② 分组只有 2 个（lookbehind 不算捕获组），
# 原先误写成 `\d{4}(?!\d)` 只会匹配 7 位数 —— 11 位真号码一个漏掉，
# 反而把 ct=1861234 这类计数打码，污染诊断证据本身。
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_ENC_RE = re.compile(r"(enc=)[^&\s\"']+")
_CDN_RE = re.compile(r"((?:ak|at|ad)_=)[^&\s\"']+")


def _mask_phone(m: "re.Match") -> str:
    return m.group(1) + "****" + m.group(2)


def redact(text: str) -> str:
    """脱敏：手机号中段、URL 里的 enc 与 CDN 临时令牌。"""
    if not text:
        return text
    text = _PHONE_RE.sub(_mask_phone, text)
    text = _ENC_RE.sub(r"\1<REDACTED>", text)
    return _CDN_RE.sub(r"\1<REDACTED>", text)


def redact_obj(obj):
    """递归脱敏 JSON 结构中的字符串（保留结构，便于阅读）。"""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def collect_evidence(root: Path) -> "list[dict]":
    """读 evidence/chapter_*.json，按 mtime 从新到旧。"""
    ev_dir = Path(root) / "evidence"
    if not ev_dir.is_dir():
        return []
    files = [p for p in ev_dir.glob("chapter_*.json") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files:
        data = _read_json(p)
        if not isinstance(data, dict):
            continue
        res = data.get("result") or {}
        ev = data.get("evidence") or {}
        out.append({
            "file": p.name,
            "mtime": p.stat().st_mtime,
            "chapter_id": res.get("target", {}).get("chapter_id") or ev.get("meta", {}).get("chapter_id"),
            "verdict": res.get("verdict"),
            "failure_stage": res.get("failure_stage"),
            "crash": res.get("crash"),
            "passed_count": res.get("passed_count"),
            "retry_count": res.get("retry_count"),
            "timing_s": res.get("timing_s"),
            "created_at_utc": res.get("created_at_utc"),
            "app_version": ev.get("meta", {}).get("app_version"),
            "failed_checks": [k for k in CHECK_ORDER if ev.get("checks", {}).get(k) is False],
            "max_currentTime": ev.get("max_currentTime"),
            "video_duration": ev.get("video_duration"),
            "ml_log_count": ev.get("ml_log_count"),
            "isPassed_seen": ev.get("checks", {}).get("isPassed_seen"),
            "console_tail": (ev.get("diagnostics", {}) or {}).get("at_end_console_tail") or [],
            "errors": ev.get("errors") or [],
        })
    return out


def collect_registry(root: Path) -> "list[dict]":
    """读 state/**/registry/*/tasks.json，汇总熔断与失败任务。"""
    state = Path(root) / "state"
    if not state.is_dir():
        return []
    rows: "dict[str, dict]" = {}
    for p in state.glob("**/registry/*/tasks.json"):
        data = _read_json(p)
        if not isinstance(data, dict):
            continue
        tasks = data.values() if all(isinstance(v, dict) for v in data.values()) else []
        for t in tasks:
            tid = t.get("task_id") or "?"
            cf = int(t.get("consecutive_failures") or 0)
            status = t.get("status") or "?"
            row = rows.setdefault(tid, {
                "task_id": tid, "chapter_id": t.get("chapter_id"),
                "title": t.get("title"), "status": status,
                "consecutive_failures": cf, "failure_stage": "",
                "last_failure_at_utc": t.get("last_failure_at_utc") or "",
            })
            row["consecutive_failures"] = max(row["consecutive_failures"], cf)
            if status == "BLOCKED" or cf > 0:
                row["status"] = status if status == "BLOCKED" else row["status"]
            row["failure_stage"] = row["failure_stage"] or (t.get("failure") or {}).get("stage", "")
            row["last_failure_at_utc"] = row["last_failure_at_utc"] or (t.get("last_failure_at_utc") or "")
    return sorted(rows.values(), key=lambda r: -r["consecutive_failures"])


def summarize(root: Path, *, loop_log_lines: int = 60) -> str:
    """生成人可读的诊断摘要 Markdown。root = exe 旁目录（可写根）。"""
    root = Path(root)
    bi = build_info()
    parts: "list[str]" = []

    parts.append("# xuexitong 故障诊断摘要\n")
    parts.append(f"- 应用版本：**{bi['app_version']}**"
                 + (f"（git {bi['git_sha']}）" if bi["git_sha"] else ""))
    parts.append(f"- 收集时间：{bi['built_at_utc']}")
    parts.append(f"- 运行根目录：`{root}`")
    parts.append(f"- 运行环境：{'Windows 冻结 exe' if bi['frozen'] else '源码形态'}")
    parts.append(f"- exe：{bi['executable'] or '(非冻结形态)'}\n")

    # ── 逐章证据 ──
    evs = collect_evidence(root)
    if not evs:
        parts.append("## 1. 章节证据\n\n**未找到任何 `evidence/chapter_*.json`** —— "
                     "说明程序还没跑到产出证据那一步（多半是登录/启动阶段就退出了）。"
                     "请重点看下面第 3 节的 loop.log 与 registry 段。\n")
    else:
        parts.append("## 1. 章节证据（按时间倒序，最多 10 条）\n")
        for e in evs[:10]:
            stage = e.get("failure_stage") or "(未判定)"
            head = (f"### {e['file']}  ·  章 `{e.get('chapter_id')}`  ·  "
                    f"verdict={e.get('verdict')}  ·  stage=`{stage}`")
            parts.append(head + "\n")
            if stage in STAGE_MEANING:
                phen, cause = STAGE_MEANING[stage]
                parts.append(f"- **现象**：{phen}\n- **最可能原因**：{cause}\n")
            if e.get("crash"):
                parts.append(f"- **崩溃**：`{e['crash']}`\n")
            if e.get("failed_checks"):
                parts.append(f"- 未通过的检查项：`{'`, `'.join(e['failed_checks'])}`\n")
            if e.get("max_currentTime") is not None:
                parts.append(f"- 播放进度：currentTime={e['max_currentTime']}s / "
                             f"duration={e.get('video_duration')}s，"
                             f"心跳 ml_log_count={e.get('ml_log_count')}\n")
            parts.append(f"- 用时 {e.get('timing_s')}s，重试 {e.get('retry_count')} 次，"
                         f"时间 {e.get('created_at_utc')}\n")
            if e.get("errors"):
                parts.append(f"- errors：`{e['errors'][:3]}`\n")

    # ── 账本/熔断 ──
    regs = collect_registry(root)
    blocked = [r for r in regs if r.get("status") == "BLOCKED" or r.get("consecutive_failures", 0) >= 3]
    parts.append("## 2. 任务账本（熔断/连续失败）\n")
    if not regs:
        parts.append("未找到 `state/**/registry/*/tasks.json`。\n")
    else:
        total_cf = sum(r.get("consecutive_failures", 0) for r in regs)
        parts.append(f"- 任务总数 {len(regs)}，连续失败累计 {total_cf}，"
                     f"熔断(BLOCKED 或连续失败≥3) {len(blocked)} 条\n")
        if blocked:
            parts.append("\n| 任务 | 标题 | 状态 | 连续失败 | 失败阶段 |\n|---|---|---|---|---|")
            for r in blocked[:20]:
                parts.append(f"| `{r['task_id']}` | {r.get('title') or ''} | {r['status']} | "
                             f"{r['consecutive_failures']} | `{r.get('failure_stage') or '-'}` |")
            parts.append("")
        else:
            parts.append("- 没有熔断任务。\n")

    # ── 调度层日志尾部 ──
    log_path = root / "evidence" / "loop.log"
    parts.append("## 3. 调度层日志尾部（loop.log，决定「这一轮为什么没推进」）\n")
    from utils.runlog import read_tail
    tail = read_tail(log_path, loop_log_lines)
    if tail:
        parts.append("```\n" + redact("\n".join(tail)) + "\n```\n")
    else:
        parts.append("未找到 `evidence/loop.log`（旧版本无此文件；新版本运行一次即生成）。\n")

    parts.append("---\n本摘要由 `utils/diag_summary.py` 自动生成，仅陈述观测到的现象，"
                 "不含任何猜测性修复。所有手机号/enc/CDN 临时令牌均已脱敏。")
    return redact("\n".join(parts))


def main(argv=None) -> int:
    """python -m utils.diag_summary <解压后的诊断包目录> [-o 输出.md]"""
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="python -m utils.diag_summary",
        description="把 xuexitong 诊断包汇总成一份可直接读的 Markdown 摘要")
    ap.add_argument("root", type=Path, help="诊断包解压后的目录（含 evidence/ 与 state/）")
    ap.add_argument("--loop-log-lines", type=int, default=60,
                    help="loop.log 取尾部多少行（默认 60）")
    ap.add_argument("-o", "--output", type=Path, help="写入文件；缺省打到标准输出")
    args = ap.parse_args(argv)

    root = args.root
    if not root.is_dir():
        print(f"[diag_summary] 目录不存在：{root}", file=sys.stderr)
        return 2

    text = summarize(root, loop_log_lines=args.loop_log_lines)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"[diag_summary] 已写出：{args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())