# -*- coding: utf-8 -*-
"""只读：打开视频号内容列表，列出所有「将于…发表」的定时作品，并和发布记录对账。
py -3 list_scheduled.py"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_post, sph_log, sph_env  # noqa: E402
from playwright.sync_api import sync_playwright

sph_post.kill_profile_chrome()
chrome = sph_post.start_chrome()
try:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{sph_post.CDP_PORT}")
        page = b.contexts[0].pages[0]
        page.goto(sph_post.LIST_URL, wait_until="domcontentloaded")
        js = """() => { const R = document.querySelector('wujie-app')?.shadowRoot;
          return (R ? [...R.children].map(e => e.innerText).join('\\n') : document.body.innerText); }"""
        txt = ""
        for _ in range(20):   # 内容列表在微前端里加载，最多等 40 秒，出现作品列表再读
            page.wait_for_timeout(2000)
            # goto 带重定向返回时 url 可能还是旧的，所以登录判定放在轮询里
            if "login" in page.url:      # 登录态过期：不然后面会误报「平台上 0 条」
                print("[NEED_LOGIN] 视频号登录态已过期，先跑 login.py 扫码，再重新对账", flush=True)
                sph_env.set_status(sph_env.NEED_LOGIN, url=page.url, from_="list_scheduled")
                page.screenshot(path=str(sph_env.tmp_dir() / "list_page.png"))
                b.close()
                sys.exit(2)
            txt = page.evaluate(js) or ""
            if "发表" in txt and len(txt) > 200:
                page.wait_for_timeout(1500)
                txt = page.evaluate(js) or txt
                break
        print("页面地址：", page.url)
        page.screenshot(path=str(sph_env.tmp_dir() / "list_page.png"))
        b.close()
finally:
    chrome.terminate()
    sph_post.kill_profile_chrome()

(sph_env.tmp_dir() / "list_raw.txt").write_text(txt or "", encoding="utf-8")   # 排查用
lines = [l.strip() for l in txt.splitlines() if l.strip()]
found = []
for i, l in enumerate(lines):
    m = re.search(r"将于(\d{4})年(\d{2})月(\d{2})日 (\d{2}:\d{2})发表", l)
    if m:
        desc = l[:m.start()].strip() or (lines[i - 1] if i else "")
        found.append((f"{m[1]}-{m[2]}-{m[3]} {m[4]}", desc))
print(f"平台上定时中的作品：{len(found)} 条")
for t, d in sorted(found):
    print(" ", t, "|", d)
from collections import Counter
from datetime import datetime
now = datetime.now().strftime("%Y-%m-%d %H:%M")
# 已经过了发表时间的会变成普通作品，不再显示「将于…发表」，只对账未来的
logged = {(r["time"], r["desc"]) for r in sph_log.load() if r.get("time", "") > now}
plat = set(found)
for (t, d), n in Counter(found).items():
    if n > 1:
        print(f"⚠ 平台上重复 {n} 条：", t, d)
for t, d in sorted(logged - plat):
    print("记录里有、平台上没找到：", t, d)
for t, d in sorted(plat - logged):
    print("平台上有、记录里没有：", t, d)
