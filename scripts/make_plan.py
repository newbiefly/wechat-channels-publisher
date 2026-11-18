# -*- coding: utf-8 -*-
"""生成/增量更新视频号定时发表计划（只排期，不发布）。

用法：
  py -3 make_plan.py --dir <视频目录> [--include 关键词 ...] [--per-day 1] [--start 2026-11-23]
  py -3 make_plan.py --confirm          # 给当前计划签字确认（人看过之后才跑这个）
  py -3 make_plan.py --show             # 只打印当前计划

增量规则（默认，--reset 关闭）：
  - 已发布的视频（发布记录里有）→ 不排
  - 已排期但还没发的视频（plan.json 里）→ 保持原日期时间不动，不会被重排
  - 只有新视频才占用新的空档日期
  所以随时可以「把新视频丢进目录，再跑一次」，老计划不会被打乱。

安全约束：写完计划会清掉签名。没签字的计划，sph_post.py --publish 会拒绝执行。
"""
import argparse, os, random, sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

for s in (sys.stdout, sys.stderr):
    s.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_log, sph_env, sph_plan  # noqa: E402

WEEK = "一二三四五六日"

ap = argparse.ArgumentParser()
ap.add_argument("videos", nargs="*")
ap.add_argument("--dir")
ap.add_argument("--include", nargs="*", default=[])
ap.add_argument("--start", help="从哪天开始找空位，默认明天")
ap.add_argument("--at", help="固定时间 HH:MM（和 --window 二选一）")
ap.add_argument("--window", default="07:30-08:30", help="每天随机时间段，默认 07:30-08:30")
ap.add_argument("--per-day", type=int, default=1)
ap.add_argument("--gap", type=int, default=120, help="固定时间模式下同一天多条的间隔分钟")
ap.add_argument("--seed", type=int, help="随机种子")
ap.add_argument("--pattern", help="每天条数循环，如 1,2")
ap.add_argument("--slots", help="时间段，逗号分隔，如 07:30-08:00,12:00-12:30")
ap.add_argument("--reset", action="store_true", help="丢掉现有计划重排（默认增量）")
ap.add_argument("--confirm", action="store_true", help="给当前计划签字确认")
ap.add_argument("--show", action="store_true", help="只打印当前计划")
a = ap.parse_args()


# ---------------------------------------------------------------- 只读 / 签字
def show_table(items):
    if not items:
        print("（空）")
        return
    print(f"{'时间':<20}{'星期'}  标题")
    for it in sorted(items, key=lambda x: x["time"]):
        dt = datetime.strptime(it["time"], "%Y-%m-%d %H:%M")
        print(f"  {it['time']:<18}{WEEK[dt.weekday()]}     {Path(it['video']).stem}{it.get('_tag', '')}")


if a.show:
    items = sph_plan.load()
    ok, why = sph_plan.check(items)
    print(f"当前计划 {len(items)} 条 · 签名状态：{why}")
    show_table(items)
    sys.exit(0)

if a.confirm:
    items = sph_plan.load()
    if not items:
        raise SystemExit("没有计划可确认，先跑 make_plan.py 生成")
    meta = sph_plan.confirm(items)
    print(f"✓ 已确认 {meta['count']} 条，指纹 {meta['digest']}（{meta['confirmed_at']}）")
    print("  现在可以执行：py -3 sph_post.py --batch <plan.json> --publish")
    sys.exit(0)


# ---------------------------------------------------------------- 收集视频
vids = [os.path.abspath(v) for v in a.videos]
if a.dir:
    vids += sorted(str(p) for p in Path(a.dir).glob("*.mp4"))
if a.include:
    vids = [v for v in vids if any(k in Path(v).stem for k in a.include)]
missing = [v for v in vids if not os.path.exists(v)]
if missing:
    raise SystemExit("找不到：\n" + "\n".join(missing))

rows = sph_log.load()
done = sph_log.done_videos(rows)
published_days = sph_log.day_counts(rows)

# 已排期但没发的：保持原样（增量核心）
kept, kept_keys = [], set()
if not a.reset:
    for it in sph_plan.load():
        k = sph_log.video_key(it["video"])
        if k in done or k in kept_keys:
            continue                      # 已发过 / 重复条目 → 丢掉
        it = dict(it, _tag="（沿用）")
        kept.append(it)
        kept_keys.add(k)

# 新视频：不在发布记录、也不在现有计划里
todo, seen = [], set(kept_keys)
for v in vids:
    k = sph_log.video_key(v)
    if k in done:
        print("跳过（已发过）：", Path(v).name)
        continue
    if k in seen:
        if k not in kept_keys:
            print("跳过（清单内重复）：", Path(v).name)
        continue
    seen.add(k)
    todo.append(v)

if not todo:
    print(f"没有新视频要排。现有计划 {len(kept)} 条（用 --show 查看）")
    if kept:
        sph_plan.save(kept)
        print()
        show_table(kept)
        print("\n⚠ 计划已刷新，签名清空。给用户过目后跑：py -3 make_plan.py --confirm")
    sys.exit(0)

# ---------------------------------------------------------------- 排期
rng = random.Random(a.seed)
tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
day = max(datetime.strptime(a.start, "%Y-%m-%d"), tomorrow) if a.start else tomorrow
w_lo, w_hi = [datetime.strptime(x, "%H:%M") for x in a.window.split("-")]
lo_min, hi_min = w_lo.hour * 60 + w_lo.minute, w_hi.hour * 60 + w_hi.minute


def mins(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


pattern = [int(x) for x in a.pattern.split(",")] if a.pattern else None
slot_ranges = [tuple(mins(t) for t in s.split("-")) for s in a.slots.split(",")] if a.slots else None

# 占用统计：已发布的 + 已排期的
used = Counter(published_days)
taken = {}                                # 某天已占的分钟，用来避让
for it in kept:
    d = it["time"][:10]
    used[d] += 1
    taken.setdefault(d, []).append(mins(it["time"][11:16]))
for r in rows:
    t = r.get("time") or ""
    if len(t) == 16:
        taken.setdefault(t[:10], []).append(mins(t[11:]))

new_items, occupied_days = [], []
i, k = 0, 0
while i < len(todo):
    ds = day.strftime("%Y-%m-%d")
    cap = pattern[k % len(pattern)] if pattern else a.per_day
    k += 1
    if slot_ranges:
        busy = taken.get(ds, [])
        free_slots = [s for s in slot_ranges[:cap] if not any(s[0] <= x <= s[1] for x in busy)]
        if not free_slots:
            occupied_days.append(ds)
            day += timedelta(days=1)
            continue
        for lo, hi in free_slots[:len(todo) - i]:
            t = day + timedelta(minutes=rng.randint(lo, hi))
            new_items.append({"video": todo[i], "time": t.strftime("%Y-%m-%d %H:%M"), "_tag": "（新增）"})
            i += 1
        day += timedelta(days=1)
        continue
    free = cap - used.get(ds, 0)
    if free <= 0:
        occupied_days.append(ds)
        day += timedelta(days=1)
        continue
    n = min(free, len(todo) - i)
    if a.at:
        h, m = map(int, a.at.split(":"))
        slots = [day + timedelta(hours=h, minutes=m + a.gap * j) for j in range(n)]
    else:
        picks = []
        while len(picks) < n:
            x = rng.randint(lo_min, hi_min)
            if all(abs(x - y) >= 10 for y in picks) and all(abs(x - y) >= 10 for y in taken.get(ds, [])):
                picks.append(x)
        slots = [day + timedelta(minutes=x) for x in sorted(picks)]
    for t in slots:
        new_items.append({"video": todo[i], "time": t.strftime("%Y-%m-%d %H:%M"), "_tag": "（新增）"})
        i += 1
    day += timedelta(days=1)

items = kept + new_items
sph_plan.save(items)

for d in occupied_days:
    print("跳过日期（已排满）：", d)
print(f"共 {len(items)} 条（沿用 {len(kept)}，新增 {len(new_items)}）→ {sph_plan.plan_path()}")
show_table(items)
print("\n⚠ 计划刚生成/更新，签名已清空。给用户过目、得到明确同意后再跑：")
print("   py -3 make_plan.py --confirm")
