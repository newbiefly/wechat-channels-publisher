# -*- coding: utf-8 -*-
"""单独的登录 / 续期：打开视频号助手，未登录就停在扫码页等你扫，登录成功后把登录态真正写盘并复核。

  py -3 login.py [--wait 300] [--settle 40] [--no-verify]

为什么要 --settle + 正常关闭：Chrome 对 cookie 写盘有几十秒的批处理延迟。扫码成功后立刻 terminate，
登录态只存在内存里，下次启动同一个 profile 照样是扫码页（实测：12:13:25 登录成功，
12:14:03 重启就回到 login.html）。所以关浏览器要用 CDP 的 Browser.close 走正常关机流程，
关掉后再启动一次复核，确认登录态真的落盘了。
"""
import argparse, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_post, sph_env  # noqa: E402
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--wait", type=int, default=300, help="等扫码的秒数（默认 300）")
ap.add_argument("--settle", type=int, default=40, help="关浏览器前等多少秒（默认 40）")
ap.add_argument("--no-verify", action="store_true", help="跳过关掉再重启的复核（省约 20 秒）")
a = ap.parse_args()
sph_post.LOGIN_WAIT = a.wait

print("数据目录：", sph_env.data_root(), flush=True)

# 注意：不能用 with sync_playwright()，它退出时会停掉 Playwright，浏览器连接随之失效
pw = sync_playwright().start()


def open_browser():
    sph_post.kill_profile_chrome()
    ch = sph_post.start_chrome()
    b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{sph_post.CDP_PORT}")
    page = b.contexts[0].pages[0] if b.contexts[0].pages else b.contexts[0].new_page()
    return ch, b, page


def graceful_close(page, proc, timeout=40):
    """让 Chrome 走正常关机流程（Browser.close），cookie 才会落盘。
    直接 terminate 是硬杀，内存里的登录态会丢。"""
    try:
        page.context.new_cdp_session(page).send("Browser.close")
    except Exception as e:
        print("  （Browser.close 发不出去，退回 terminate：%s）" % str(e)[:80], flush=True)
    try:
        proc.wait(timeout=timeout)      # 等它自己退干净
        return
    except Exception:
        pass
    proc.terminate()


rc = 0
try:
    # ------------------------------------------------------------ 1. 登录
    chrome, browser, page = open_browser()
    try:
        sph_post.ensure_login(page)
    except SystemExit as e:
        # 超时：窗口留着，用户随时可以扫；下次跑任意脚本会自动接管/清理它
        print("✗", e, flush=True)
        print("  浏览器窗口我没关，你随时可以继续扫码；扫完回一声，重跑本命令即可。", flush=True)
        browser.close()
        sys.exit(2)

    print("  navigator.webdriver =", page.evaluate("navigator.webdriver"), flush=True)
    if a.settle:
        print(f"  等 {a.settle} 秒再关浏览器（cookie 写盘有延迟）…", flush=True)
        time.sleep(a.settle)
    print("  正常关闭浏览器，触发登录态落盘…", flush=True)
    graceful_close(page, chrome)      # 必须先发 Browser.close；断了连接就发不出去了
    browser.close()
    sph_post.kill_profile_chrome()
    time.sleep(2)

    if a.no_verify:
        print("✓ 登录完成（未复核）。", flush=True)
        sys.exit(0)

    # ------------------------------------------------------------ 2. 复核：重启后还是不是登录态
    print("  重启浏览器复核登录态…", flush=True)
    sph_post.LOGIN_WAIT = 10
    chrome2, browser2, page2 = open_browser()
    try:
        sph_post.ensure_login(page2)
        print("✓ 登录态已落盘并通过复核，下次不用再扫码。", flush=True)
        rc = 0
    except SystemExit:
        print("✗ 登录态没落盘：重启后又是扫码页。加大 --settle（比如 90）再试。", flush=True)
        print(f"  登录态目录：{sph_post.PROFILE}", flush=True)
        rc = 3
    finally:
        graceful_close(page2, chrome2)
        browser2.close()
        sph_post.kill_profile_chrome()
finally:
    try:
        pw.stop()
    except Exception:
        pass

sys.exit(rc)
