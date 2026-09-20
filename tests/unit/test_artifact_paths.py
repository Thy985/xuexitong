"""产物路径必须由 `artifact_slug` 生成 —— 点级 task_id 含 `:`。

第 4 轮之后的 D7 复验实测（ACCEPTANCE §4.7 / D10）：`_run_one_chapter` 直接把
task_id 拼进文件名（`./evidence/chapter_{task_id}.json`），而点级记录的 id 形如
`1217304708:video2`。Windows 把 `name:stream` 解释成 **NTFS 备用数据流**，于是
`dir /r` 看到的是：

    0              chapter_1217304708            ← 0 字节空壳
    28,529 :$DATA  chapter_1217304708:video2.json

产物落进了同章 `<cid>` 那个空文件的隐藏流里：`_archive_existing()` 归档的是 0 字节
空壳，D3 对点级任务失效。Linux runner 上 `:` 是合法字符 → 路径正常，**又一处本地/云不对称**。
"""
import os

import pytest

from scheduler.scheduler import artifact_slug

REAL_IDS = ["1217304708", "1217304708:video2", "1217304738:other", "1217304708:video10"]


def test_colon_is_never_left_in_the_slug():
    for tid in REAL_IDS:
        slug = artifact_slug(tid)
        assert ":" not in slug, f"{tid} -> {slug}：Windows 会把 : 当数据流分隔符"
        assert os.sep not in slug and "/" not in slug


def test_plain_chapter_id_is_unchanged():
    """章级 id 不该被无谓改写 —— 历轮产物名要保持可比对。"""
    assert artifact_slug("1217304708") == "1217304708"


def test_sibling_points_get_distinct_slugs():
    """`video2` 与 `other` 必须落在不同文件上，否则同章两点互相抹掉。"""
    slugs = {artifact_slug(t) for t in REAL_IDS}
    assert len(slugs) == len(REAL_IDS), slugs


@pytest.mark.parametrize("tid", REAL_IDS)
def test_slug_is_a_legal_windows_filename(tid):
    """NTFS 非法字符集里不含 `:` 之外的检查也要过：不能以点/空格结尾。"""
    name = f"chapter_{artifact_slug(tid)}.json"
    assert name == name.rstrip(" .")
    assert not any(c in name for c in '<>:"|?\\/')
