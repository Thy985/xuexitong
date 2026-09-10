"""E6.1 — Registry Migration/Repair (state/migrations).

历史污染修复：把旧的、只有「nextUnit / URL chapterId 变化 / 页面导航推断」或
完全没有 completion evidence 的 COMPLETED 条目修正为 UNKNOWN（需重新验证）。
绝不删除 Execution History，只修正 Registry canonical state。

强证据（SERVER_VERIFIED / RECHECK）保留 COMPLETED；
UI(服务器 DOM `completed` 标记) 保留 COMPLETED(UI)；
无证据 → 降级 UNKNOWN。

CLI：python -m state.migrations.repair [--course <key>] [--apply]
Repair evidence 写到 state/migrations/<ts>/<course>json。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "state" / "migrations"
_REGISTRY_ROOT = _REPO_ROOT / "state" / "registry"


def _evidence_level(record) -> str:
    """取任务当前最可信的证据强度（completion_evidence 优先，回退 verification）。"""
    cev = getattr(record, "completion_evidence", None)
    if cev is not None:
        lvl = cev.type
        if lvl not in ("", "NONE"):
            return lvl or "NONE"
    ver = getattr(record, "verification", None)
    if ver is not None:
        return ver.level or "NONE"
    return "NONE"


def has_sufficient_evidence(record) -> bool:
    """判断一条任务是否有可保留 COMPLETED 的完成证据。"""
    return _evidence_level(record) in ("SERVER_VERIFIED", "RECHECK", "UI")


def list_course_keys() -> list[str]:
    if not _REGISTRY_ROOT.exists():
        return []
    return [d.name for d in _REGISTRY_ROOT.iterdir()
            if d.is_dir() and (d / "tasks.json").exists()]


def load_registry_file(course_key: str) -> dict:
    p = _REGISTRY_ROOT / course_key / "tasks.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def repair_course(course_key: str) -> tuple[dict, dict]:
    """对单个课程的 registry 做修复。

    Returns:
        (new_registry_mapping, report)
    """
    from app.registry.task_registry import TaskRecord

    raw = load_registry_file(course_key)
    downgraded = 0
    retained = 0
    repair_map: dict = {}
    new_raw: dict = {}

    for tid, d in raw.items():
        try:
            t = TaskRecord.from_dict(dict(d))
        except Exception:
            new_raw[tid] = d
            continue

        if t.status == "COMPLETED" and not has_sufficient_evidence(t):
            # 污染修复：无足够证据 → 降级 UNKNOWN（需重新验证）
            nd = dict(d)
            nd["status"] = "UNKNOWN"
            nd.setdefault("_repair", {})["migration"] = {
                "changed_from": "COMPLETED",
                "changed_to": "UNKNOWN",
                "reason": "no completion_evidence (SERVER_VERIFIED/RECHECK/UI)",
            }
            new_raw[tid] = nd
            downgraded += 1
            repair_map[tid] = nd["_repair"]["migration"]
        else:
            new_raw[tid] = d
            retained += 1

    report = {
        "course": course_key,
        "downgraded": downgraded,
        "kept_completed": retained,
        "repair_map": repair_map,
    }
    return new_raw, report


def run(cli_keys: list[str] | None = None, apply: bool = False) -> dict:
    """执行修复，返回整体报告（含 repair evidence 输出目录）。

    - apply=True：改写 canonical registry；并把修复后 registry 快照写入 migrations 目录。
    - apply=False（dry-run）：只生成修复报告（不写 canonical registry），
      但仍把 dry-run 报告落盘到 state/migrations/<ts>/report.json 作为审计。
    """
    keys = cli_keys or list_course_keys()
    if not keys:
        return {"error": "no course keys to repair", "evidence_dir": None}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _MIGRATIONS_DIR / ts

    aggregate = []
    for ck in keys:
        new_raw, report = repair_course(ck)
        aggregate.append(report)
        out_dir.mkdir(parents=True, exist_ok=True)
        if apply:
            (out_dir / f"{ck}.json").write_text(
                json.dumps(new_raw, ensure_ascii=False, indent=2), encoding="utf-8")
            # 写回 canonical registry
            p = _REGISTRY_ROOT / ck / "tasks.json"
            p.write_text(json.dumps(new_raw, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "ts": ts,
        "dry_run": not apply,
        "courses": aggregate,
        "total_downgraded": sum(r["downgraded"] for r in aggregate),
        "evidence_dir": str(out_dir),
    }
    (out_dir / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="E6.1 registry repair/migration")
    ap.add_argument("--course", default=None, help="课程 key；省略则扫描全部")
    ap.add_argument("--apply", action="store_true",
                    help="实际写回修复（默认仅输出报告/repair evidence）")
    args = ap.parse_args()
    keys = [args.course] if args.course else None
    print(json.dumps(run(keys, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()