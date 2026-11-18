# -*- coding: utf-8 -*-
"""定时计划的读写 + 确认签名。

设计目的：把「用户确认后才发布」做成技术硬约束，而不是靠人记着。

  plan.json      计划本体（纯 list，兼容 sph_post.py --batch 的旧格式）
  plan.meta.json 确认签名 {digest, count, confirmed_at}

digest = 所有 (视频名, 时间) 排序后的 sha1。用户确认时记一次，发布前再算一次：
  - 没有 meta        → 计划还没确认过，拒绝发布
  - digest 不一致    → 确认之后计划被改过，拒绝发布
  - digest 一致      → 放行

这样即使中途有人（包括我）往 plan.json 里塞了新条目，也会被拦下来重新过目。
"""
import hashlib, json
from datetime import datetime
from pathlib import Path

import sph_env


def plan_path() -> Path:
    return sph_env.tmp_dir() / "plan.json"


def meta_path() -> Path:
    return sph_env.tmp_dir() / "plan.meta.json"


def load() -> list:
    p = plan_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else data.get("items", [])


def save(items: list):
    """写计划，并清掉旧签名（内容变了，之前那次确认就失效）。
    下划线开头的字段（如 _tag）只是展示用的，不落盘。"""
    p = plan_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    p.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path().unlink(missing_ok=True)


def digest(items: list) -> str:
    key = "\n".join(sorted(f"{Path(i['video']).stem}|{i['time']}" for i in items))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def confirm(items: list) -> dict:
    meta = {"digest": digest(items), "count": len(items),
            "confirmed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def check(items: list):
    """返回 (ok, 原因)。发布前调用。"""
    if not items:
        return False, "计划是空的"
    mp = meta_path()
    if not mp.exists():
        return False, "这份计划还没确认过（没有 plan.meta.json）"
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return False, "plan.meta.json 损坏"
    if meta.get("digest") != digest(items):
        return False, (f"计划在你确认之后被改过（确认 {meta.get('count')} 条 / 现在 {len(items)} 条，"
                       f"指纹 {meta.get('digest')} → {digest(items)}），请重新过目并确认")
    return True, f"已确认（{meta['count']} 条，{meta['confirmed_at']}）"
