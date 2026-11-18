# -*- coding: utf-8 -*-
"""发布记录（.agents/sph-publish-log.jsonl）的读写，make_plan.py 和 sph_post.py 共用。

去重两个维度：
  视频：同一路径，或文件名相同（换了目录/复制一份也算同一条）→ 不再发
  日期：某天已排满 per_day 条 → 这天不再排，自动顺延到下一个空日子
"""
import json, os, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_env  # noqa: E402

DATA = sph_env.data_root()          # <工作区>/.agents（可用 SPH_DATA_DIR 改）
ROOT = DATA.parent                  # 兼容旧代码里 sph_log.ROOT 的用法
LOG_FILE = DATA / "sph-publish-log.jsonl"


def load():
    rows = []
    if LOG_FILE.exists():
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def video_key(path):
    return Path(path).stem.strip().lower()


def done_videos(rows=None):
    rows = load() if rows is None else rows
    return {video_key(r["video"]) for r in rows if r.get("video")}


def day_counts(rows=None):
    """每天已定时几条（只算今天及以后，过去的不占位）。"""
    rows = load() if rows is None else rows
    today = datetime.now().strftime("%Y-%m-%d")
    c = Counter()
    for r in rows:
        d = (r.get("time") or "")[:10]
        if d >= today:
            c[d] += 1
    return c


def append(job, verified=True):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"video": job["video"], "desc": job["desc"], "title": job["title"],
                            "time": job["time"], "original": job["original"], "verified": verified,
                            "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False) + "\n")
