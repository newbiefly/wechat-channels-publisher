# -*- coding: utf-8 -*-
"""
视频号定时发表（Playwright 驱动独立 Chrome）

用法：
  python sph_post.py <视频路径> "<YYYY-MM-DD HH:MM>" [--desc 描述] [--title 短标题] [--no-original] [--publish]
  python sph_post.py --batch <清单.json> [--publish] [--force]

默认值：描述 = 视频文件名（去扩展名）；短标题 = config.json 的 default_short_title；
位置 = 不显示位置；声明原创 = 是。
不带 --publish：填完、回读、截图后停下，不点发表，浏览器保留 --hold 秒（默认 600）供人工查看。
带 --publish：回读全部通过才点「发表」，再到内容列表确认出现「将于…发表」才算成功，并写发布记录。
已在发布记录里的视频默认跳过（防重复发），--force 才会再发。

清单 JSON：[{"video": "...mp4", "time": "2026-11-08 08:00", "desc": "可选", "title": "可选", "original": true}, ...]

数据目录（在工作区 .agents 下，不在 skill 目录里）：
  .agents/sph-profile/          独立 Chrome 配置（保存登录，勿分享）
  .agents/sph-publish-log.jsonl 发布记录（一行一条）
  .agents/tmp/sph/shots/        每条的表单截图
"""
import argparse, json, os, random, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

for s in (sys.stdout, sys.stderr):
    s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_env  # noqa: E402  数据目录 + 运行状态
import sph_plan  # noqa: E402  计划的确认签名

DATA = sph_env.data_root()          # 默认 <工作区>/.agents，可用 SPH_DATA_DIR 或 config.json 改
ROOT = DATA.parent
PROFILE = DATA / "sph-profile"
LOG_FILE = DATA / "sph-publish-log.jsonl"
SHOTS = DATA / "tmp" / "sph" / "shots"
CREATE_URL = "https://channels.weixin.qq.com/platform/post/create"
LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
DEFAULT_SHORT_TITLE = sph_env.get("default_short_title", "日常分享")   # 平台限 16 字，可在 config.json 改
SHORT_TITLE_MAX = 16


def clean_stem(stem):
    """文件名末尾的版本后缀不进描述：`xxx_v2` → `xxx`，`xxx -v3` / `xxx_副本` 同理。"""
    out = re.sub(r"[\s_\-]*(v\d+|副本|copy|final)+\s*$", "", stem, flags=re.I).strip()
    return out or stem


def log(*x):
    print(time.strftime("%H:%M:%S"), *x, flush=True)


# ---------------------------------------------------------------- 拟人节奏（--no-jitter 关闭）

JITTER = True


def pause(page, lo=0.5, hi=1.2):
    """步骤之间随机停一下（整条控制在 30s 内）。"""
    if JITTER:
        page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


def human_click(page, loc):
    """先把鼠标移过去、停一下再点（Playwright 的 click 本身是瞬移）。"""
    loc.wait_for(state="visible", timeout=8000)   # 下拉/面板有展开动画，先等可见
    if JITTER:
        box = loc.bounding_box(timeout=5000)
        if box and not (0 <= box["y"] <= page.viewport_size["height"] - box["height"] if page.viewport_size else True):
            loc.scroll_into_view_if_needed(timeout=5000)
            box = loc.bounding_box(timeout=5000)
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            page.mouse.move(x, y, steps=random.randint(5, 10))
            page.wait_for_timeout(random.randint(80, 200))
    loc.click()


def human_type(page, text):
    """逐字输入，每字 30–80ms。"""
    if JITTER:
        for ch in text:
            page.keyboard.insert_text(ch)
            page.wait_for_timeout(random.randint(30, 80))
    else:
        page.keyboard.insert_text(text)


# ---------------------------------------------------------------- 发布记录

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sph_log  # noqa: E402  共用的发布记录（视频去重 + 日期占位）


# ---------------------------------------------------------------- 浏览器

def kill_profile_chrome():
    """上次没关干净的独立 Chrome 会占住配置目录，新实例会秒退。只杀用本配置目录的进程，不碰日常 Chrome。"""
    if os.name != "nt":
        return
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{PROFILE.name}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    time.sleep(1.5)


CDP_PORT = 9333
CHROME_CANDIDATES = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]


def start_chrome():
    """像手动打开一样启动 Chrome，只多一个调试端口；不带 Playwright 的 --enable-automation 等自动化参数，
    也不会出现「Chrome 正受到自动测试软件的控制」提示条。脚本再通过 CDP 连上去。"""
    exe = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not exe:
        raise SystemExit("找不到 Chrome，请安装 Google Chrome")
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([exe, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
                             "--no-first-run", "--no-default-browser-check", "--window-size=1400,950",
                             "about:blank"])
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("Chrome 调试端口没起来")


LOGIN_WAIT = 300   # 扫码等待秒数（--login-wait 可调）
LOGIN_GRACE = 10   # 打开后先等这么久再判定「未登录」，不然页面还在跳转就会误报


def ensure_login(page):
    """打开创作页；未登录就停在扫码页等用户扫，最多 LOGIN_WAIT 秒。

    未登录期间：
      - 往 stdout 打一行带 [NEED_LOGIN] 标记的话（上层据此立刻提示用户）
      - 写 status.json 的 state=need_login（上层可轮询，不必解析输出）
      - 每 30 秒报一次剩余时间

    LOGIN_GRACE 秒内不报「未登录」：页面跳转/微前端挂载期间 URL 和表单都可能是中间态，
    实测会先报未登录、9 秒后同一页面又变成已登录，害用户白扫一次。
    """
    t0 = time.time()
    page.goto(CREATE_URL, wait_until="domcontentloaded")
    deadline = t0 + LOGIN_WAIT
    announced, last_tick = False, None
    while True:
        try:
            # 已登录：不在登录页，且发表页表单渲染出来了（不看上传框——残留上传时它不存在）
            if "login" not in page.url and page.evaluate(RESIDUE_JS) is not None and \
                    page.locator(".label", has_text="视频描述").count():
                if announced:
                    log("✓ 登录态已就绪")
                sph_env.set_status(sph_env.RUNNING, url=page.url)
                return
        except Exception:
            pass  # 页面正在跳转/重渲染，下一轮再看

        left = int(deadline - time.time())
        if left <= 0:
            sph_env.set_status(sph_env.FAILED, reason="login_timeout", url=page.url)
            raise SystemExit(
                f"等待扫码超时（{LOGIN_WAIT}s），本次没有发布任何内容。\n"
                f"浏览器窗口保留着，登录态存在 {PROFILE}；登录后再跑一次即可，不用重新上传。")
        if not announced:
            if time.time() - t0 < LOGIN_GRACE:   # 宽限期内：页面可能只是还没渲染完
                page.wait_for_timeout(1000)
                continue
            announced = True
            log("未登录，已停在视频号扫码页。")
            print(f"[NEED_LOGIN] 请在打开的 Chrome 窗口用微信扫码，扫完后在手机上点「确认登录」"
                  f"（最多等 {LOGIN_WAIT} 秒）", flush=True)
            sph_env.set_status(sph_env.NEED_LOGIN, url=page.url, wait=LOGIN_WAIT)
            last_tick = left // 30
        elif left // 30 != last_tick:
            last_tick = left // 30
            if last_tick % 2 == 0:      # 每 60 秒提示一次，别刷屏
                log(f"  等扫码… 还剩 {left}s（二维码过期就在页面上点「点击重试」刷新）")
        page.wait_for_timeout(1000)


# ---------------------------------------------------------------- 表单各步

HAS_MEDIA_JS = """() => { const R = document.querySelector('wujie-app')?.shadowRoot; if (!R) return null;
  return [...R.querySelectorAll('.label')].some(l => l.textContent.trim() === '封面预览'); }"""


RESIDUE_JS = """() => { const R = document.querySelector('wujie-app')?.shadowRoot; if (!R) return null;
  const btns = [...R.querySelectorAll('button, .weui-desktop-btn')].map(b => b.textContent.trim());
  const cover = [...R.querySelectorAll('.label')].some(l => l.textContent.trim() === '封面预览');
  const input = !!R.querySelector('input[type=file][accept*=video]');
  return {cover, input, cancel: btns.includes('取消上传'), del: btns.includes('删除')}; }"""


def clear_residue(page):
    """平台会恢复上次没完成/没清掉的上传（封面预览、0% 进度条、取消上传）。点掉它。"""
    R = "wujie-app >> "
    for label in ("取消上传", "删除"):
        btn = page.locator("button, .weui-desktop-btn", has_text=re.compile(rf"^\s*{label}\s*$")).filter(visible=True)
        if btn.count():
            btn.first.click()
            page.wait_for_timeout(800)
            # 可能弹确认框
            ok = page.locator(".weui-desktop-dialog__ft button", has_text=re.compile(r"确定|删除|取消上传|确认")).filter(visible=True)
            if ok.count():
                ok.last.click()
                page.wait_for_timeout(800)


def open_clean_form(page):
    """打开空白发表页。页面有时残留上一条视频或卡住的上传任务，先清掉再用。"""
    for _ in range(4):
        page.goto(CREATE_URL, wait_until="domcontentloaded")
        for _ in range(30):
            st = page.evaluate(RESIDUE_JS)
            if st and (st["input"] or st["cover"] or st["cancel"]):
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(1500)
        st = page.evaluate(RESIDUE_JS) or {}
        if st.get("input") and not (st.get("cover") or st.get("cancel") or st.get("del")):
            return
        log("  发表页有残留上传任务，清理", st)
        clear_residue(page)
        page.wait_for_timeout(1500)
    raise SystemExit("发表页残留清不掉，停下人工处理")


def upload(page, video, timeout=900):
    # 表单在 wujie 微前端的 open shadow DOM 里，Playwright 的 CSS 选择器能直接穿透
    page.locator("input[type=file][accept*=video]").first.set_input_files(video)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = page.evaluate("""() => {
          const R = document.querySelector('wujie-app')?.shadowRoot; if (!R) return null;
          const w = R.querySelector('.post-upload-wrap'); if (!w) return null;
          const txt = w.innerText;
          const pct = (txt.match(/(\\d+)%/) || [])[1];
          const cover = [...R.querySelectorAll('.label')].some(l => l.textContent.trim() === '封面预览');
          const del = [...R.querySelectorAll('button, .weui-desktop-btn')].some(b => b.textContent.trim() === '删除');
          return {ok: cover && del && !pct, pct, err: /上传失败|不支持该格式/.test(txt) ? txt.slice(0, 80) : null};
        }""")
        if st and st.get("err"):
            raise SystemExit(f"上传失败：{st['err']}")
        if st and st.get("ok"):
            return
        page.wait_for_timeout(1500)
    raise SystemExit("上传超时（15 分钟）")


def fill_desc(page, text):
    human_click(page, page.locator(".input-editor").first)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    human_type(page, text)
    page.locator(".label", has_text="视频描述").first.click()  # 收起话题联想


def fill_title(page, text):
    box = page.locator("input[placeholder^='填写短标题']")
    human_click(page, box)
    box.fill("")
    human_type(page, text)


def no_location(page):
    human_click(page, page.locator(".post-position-wrap .position-display"))
    pause(page, 0.2, 0.5)
    human_click(page, page.locator(".post-position-wrap .option-item .name", has_text="不显示位置")
                .filter(visible=True).first)


def set_time(page, when):
    dt = datetime.strptime(when, "%Y-%m-%d %H:%M")
    human_click(page, page.locator("label.weui-desktop-form__check-label", has_text=re.compile(r"^\s*定时\s*$")))
    pause(page, 0.2, 0.5)
    dl = page.locator("dl.weui-desktop-picker__date-time")
    human_click(page, dl.locator("input[placeholder='请选择发表时间']"))
    panel = dl.locator(".weui-desktop-picker__panel_day")
    panel.wait_for(state="visible", timeout=5000)
    for _ in range(24):  # 翻到目标年月
        labs = panel.locator(".weui-desktop-picker__panel__label").all_inner_texts()
        cy, cm = int(re.sub(r"\D", "", labs[0])), int(re.sub(r"\D", "", labs[1]))
        if (cy, cm) == (dt.year, dt.month):
            break
        btn = "right" if (cy, cm) < (dt.year, dt.month) else "left"
        human_click(page, panel.locator(f".weui-desktop-btn__icon__{btn}"))
        pause(page, 0.2, 0.5)
    day = panel.locator("table a:not(.weui-desktop-picker__faded)", has_text=re.compile(rf"^\s*{dt.day}\s*$"))
    if "disabled" in (day.get_attribute("class") or ""):
        raise SystemExit(f"{when} 这一天不可选（平台只允许未来一段时间内定时）")
    pause(page, 0.2, 0.5)
    human_click(page, day)
    pause(page, 0.2, 0.5)
    tdl = dl.locator("dl.weui-desktop-picker__time")
    human_click(page, tdl.locator("input[placeholder='请选择时间']"))
    pause(page, 0.2, 0.5)

    def pick(cls, v):
        li = tdl.locator(f"ol.{cls} li", has_text=re.compile(rf"^\s*{v:02d}\s*$"))
        if "disabled" in (li.get_attribute("class") or ""):
            raise SystemExit(f"{when} 时间不可选（需晚于当前时间）")
        li.scroll_into_view_if_needed()
        human_click(page, li)

    pick("weui-desktop-picker__time__hour", dt.hour)
    pause(page, 0.2, 0.5)
    pick("weui-desktop-picker__time__minute", dt.minute)
    pause(page, 0.2, 0.5)
    page.locator(".label", has_text="发表时间").first.click()  # 收起面板


def declare_original(page):
    item = page.locator(".form-item", has=page.locator(".label", has_text="声明原创")).first
    if not item.locator("input[type=checkbox]").first.is_checked():
        item.locator(".ant-checkbox-wrapper, label").first.click()
    # 「原创权益」弹框：勾「我已阅读并同意」→ 点「声明原创」（勾选前按钮 disabled）
    # 页面里有两份同样的弹框（一份隐藏），只取可见的那份
    dlg = page.locator(".weui-desktop-dialog",
                       has=page.locator(".weui-desktop-dialog__title", has_text="原创权益")).filter(visible=True)
    dlg.wait_for(state="visible", timeout=8000)
    agree = dlg.locator(".original-proto-wrapper input[type=checkbox]")
    if not agree.is_checked():
        dlg.locator(".original-proto-wrapper label.ant-checkbox-wrapper").click()
    page.wait_for_timeout(300)
    if not agree.is_checked():
        raise SystemExit("原创协议勾选框没勾上")
    btn = dlg.locator(".weui-desktop-dialog__ft button", has_text="声明原创")
    for _ in range(20):
        if "disabled" not in (btn.get_attribute("class") or ""):
            break
        page.wait_for_timeout(200)
    btn.click()
    dlg.wait_for(state="hidden", timeout=8000)


def readback(page):
    return page.evaluate("""() => {
      const R = document.querySelector('wujie-app').shadowRoot, q = s => R.querySelector(s);
      const radio = [...R.querySelectorAll('input.weui-desktop-form__radio')].find(r => r.checked);
      const orig = [...R.querySelectorAll('.form-item')].find(f => f.querySelector('.label')?.textContent.trim() === '声明原创');
      const pub = [...R.querySelectorAll('button')].find(b => b.textContent.trim() === '发表');
      return {
        desc: (q('.input-editor')?.innerText || '').trim(),
        title: q("input[placeholder^='填写短标题']")?.value,
        loc: (q('.post-position-wrap .option-item.active .name') || q('.post-position-wrap .location-name'))?.textContent.trim(),
        scheduled: radio?.value === '1',
        time: q("input[placeholder='请选择发表时间']")?.value,
        original: !!orig?.querySelector('input[type=checkbox]')?.checked,
        dialogOpen: [...R.querySelectorAll('.weui-desktop-dialog')].some(d => d.offsetParent !== null),
        publishEnabled: !!pub && !pub.disabled && !/disabled/.test(pub.className),
        tips: [...R.querySelectorAll('.form-item .tips, .form-item [class*=error], .form-item [class*=warn]')]
               .filter(e => e.offsetParent && e.innerText.trim()).map(e => e.innerText.trim()).slice(0, 5),
      };
    }""")


def check(rb, job):
    p = []
    if rb["desc"] != job["desc"]: p.append(f"描述不一致：{rb['desc']!r}")
    if rb["title"] != job["title"]: p.append(f"短标题不一致：{rb['title']!r}")
    if rb["loc"] != "不显示位置": p.append(f"位置：{rb['loc']}")
    if not rb["scheduled"] or rb["time"] != job["time"]: p.append(f"定时：{rb['time']}")
    if rb["original"] != job["original"]: p.append(f"原创：{rb['original']}")
    if rb["dialogOpen"]: p.append("还有弹框没关")
    if rb["tips"]: p.append(f"页面提示：{rb['tips']}")
    if not rb["publishEnabled"]: p.append("发表按钮不可点")
    return p


def verify_in_list(page, job):
    """到内容列表找「描述 + 将于 X月X日 HH:MM 发表」。"""
    dt = datetime.strptime(job["time"], "%Y-%m-%d %H:%M")
    want = f"将于{dt.year}年{dt.month:02d}月{dt.day:02d}日 {dt.hour:02d}:{dt.minute:02d}发表"
    if "post/list" not in page.url:
        page.goto(LIST_URL, wait_until="domcontentloaded")
    for _ in range(20):
        txt = page.evaluate("""() => { const R = document.querySelector('wujie-app')?.shadowRoot;
          return (R ? [...R.children].map(e => e.innerText).join(' ') : document.body.innerText).replace(/\\s+/g, ' '); }""")
        i = txt.find(job["desc"][:20])
        if i >= 0 and want in txt[i:i + 300]:
            return True
        page.wait_for_timeout(1500)
    return False


# ---------------------------------------------------------------- 主流程

def post_one(page, job, publish):
    log(f"▶ {os.path.basename(job['video'])}  定时 {job['time']}")
    job["_t0"] = time.time()
    sph_env.set_status(sph_env.RUNNING, video=os.path.basename(job["video"]), time=job["time"],
                       stage="open_form")
    for attempt in (1, 2):
        open_clean_form(page)
        pause(page, 0.5, 1.5)
        try:
            upload(page, job["video"], timeout=180)
            break
        except (SystemExit, PWTimeout) as e:
            if attempt == 2:
                raise
            log(f"  上传没完成（{str(e)[:80]}），刷新重来一次")
    log("  上传完成")
    pause(page, 0.8, 1.8)
    steps = [("描述", lambda: fill_desc(page, job["desc"])), ("短标题", lambda: fill_title(page, job["title"])),
             ("位置", lambda: no_location(page)), ("定时", lambda: set_time(page, job["time"]))]
    if job["original"]:
        steps.append(("原创", lambda: declare_original(page)))
    for name, fn in steps:
        t = time.time(); fn(); pause(page)
        log(f"  {name} {time.time() - t:.1f}s")
    page.wait_for_timeout(800)

    # 封面出来 ≠ 传完：视频还在后台上传/转码时「发表」是灰的，等它变可点
    t0 = time.time()
    while time.time() - t0 < 600:
        if readback(page)["publishEnabled"]:
            break
        page.wait_for_timeout(2000)
    log(f"  等发表按钮可点 {time.time() - t0:.0f}s")

    rb = readback(page)
    log("  回读", json.dumps({k: rb[k] for k in ("desc", "title", "loc", "time", "original", "publishEnabled")},
                           ensure_ascii=False))
    SHOTS.mkdir(parents=True, exist_ok=True)
    shot = SHOTS / (re.sub(r'[\\/:*?"<>|]', "_", job["desc"])[:40] + ".png")
    page.screenshot(path=str(shot))

    problems = check(rb, job)
    if problems:
        log("  ✗ 校验未通过，不发表：", "；".join(problems), f"（截图 {shot}）")
        sph_env.set_status(sph_env.FAILED, reason="check_failed", problems=problems,
                           video=job["video"], shot=str(shot))
        return False
    log(f"  本条到发表前耗时 {time.time() - job['_t0']:.0f}s")
    if not publish:
        log("  ✓ 校验通过（未点发表；加 --publish 才会发）")
        sph_env.set_status(sph_env.FILLED, video=job["video"], time=job["time"], shot=str(shot))
        return True

    pause(page, 1.0, 2.0)  # 「检查一遍」再点
    human_click(page, page.locator("button", has_text=re.compile(r"^\s*发表\s*$")).filter(visible=True).last)
    try:
        page.wait_for_url(re.compile(r"/platform/post/list"), timeout=60000)
    except PWTimeout:
        page.screenshot(path=str(shot.with_name(shot.stem + "_after.png")))
        log("  ✗ 点了发表但没跳到内容列表，看截图：", shot.with_name(shot.stem + "_after.png"))
        return False
    if not verify_in_list(page, job):
        # 已经跳到列表，大概率发出去了：也记一笔（verified=false），防止重跑时再发一次
        sph_log.append(job, verified=False)
        log("  ? 已跳到内容列表但没找到这条的定时记录，已按「可能已发」记录，请人工到内容管理确认")
        sph_env.set_status(sph_env.FAILED, reason="unverified_in_list", video=job["video"], time=job["time"])
        return False
    sph_log.append(job)
    log("  ✓ 已定时，内容列表已确认")
    sph_env.set_status(sph_env.PUBLISHED, video=job["video"], time=job["time"], verified=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?")
    ap.add_argument("time", nargs="?")
    ap.add_argument("--desc")
    ap.add_argument("--title")
    ap.add_argument("--no-original", action="store_true")
    ap.add_argument("--batch")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略发布记录：已发过的视频、已排满的日期也照发")
    ap.add_argument("--per-day", type=int, default=1, help="每天最多定时几条（和 make_plan 一致）")
    ap.add_argument("--hold", type=int, default=600, help="不发表时浏览器保留秒数")
    ap.add_argument("--no-jitter", action="store_true", help="关闭拟人随机节奏（仅调试用）")
    ap.add_argument("--gap", default="8-12", help="批量时两条之间随机间隔秒数，如 8-12")
    ap.add_argument("--login-wait", type=int, default=300, help="等扫码的秒数（默认 300）")
    a = ap.parse_args()
    global JITTER, LOGIN_WAIT
    JITTER = not a.no_jitter
    LOGIN_WAIT = a.login_wait
    gap_lo, gap_hi = (float(x) for x in a.gap.split("-"))

    if a.batch:
        jobs = json.load(open(a.batch, encoding="utf-8"))
    elif a.video and a.time:
        jobs = [{"video": a.video, "time": a.time, "desc": a.desc, "title": a.title, "original": not a.no_original}]
    else:
        ap.error("要么给 <视频> <时间>，要么给 --batch 清单")

    # 确认闸门：批量发布必须用一份「用户看过并签字」的计划，签完再改过也不认
    if a.batch and a.publish and not a.force:
        ok, why = sph_plan.check(jobs)
        if not ok:
            raise SystemExit(f"✗ 拒绝发布：{why}\n"
                             f"  正确顺序：make_plan.py 生成 → 给用户过目 → make_plan.py --confirm → 再发布")
        log("计划校验通过：", why)

    rows = sph_log.load()
    done = sph_log.done_videos(rows)
    used = sph_log.day_counts(rows)
    todo = []
    for j in jobs:
        j["video"] = os.path.abspath(j["video"])
        if not os.path.exists(j["video"]):
            raise SystemExit(f"找不到视频：{j['video']}")
        datetime.strptime(j["time"], "%Y-%m-%d %H:%M")
        j["desc"] = j.get("desc") or clean_stem(Path(j["video"]).stem)
        j["title"] = j.get("title") or DEFAULT_SHORT_TITLE
        j["original"] = j.get("original", True) is not False
        if len(j["title"]) > SHORT_TITLE_MAX:
            raise SystemExit(f"短标题超过 {SHORT_TITLE_MAX} 字：{j['title']}")
        if a.publish and not a.force:
            key, d = sph_log.video_key(j["video"]), j["time"][:10]
            if key in done:
                log("跳过（已发过）：", Path(j["video"]).name)
                continue
            if used[d] >= a.per_day:
                log(f"跳过（{d} 已排满 {a.per_day} 条）：", Path(j["video"]).name, "→ 重新跑 make_plan.py 排到空日子")
                continue
            done.add(key); used[d] += 1      # 同一份清单里也不重复
        todo.append(j)
    if not todo:
        log("没有要发的。"); return

    kill_profile_chrome()
    chrome = start_chrome()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ensure_login(page)
        log("浏览器自检 navigator.webdriver =", page.evaluate("navigator.webdriver"))
        results = []
        for n, j in enumerate(todo):
            if n and JITTER:
                wait = random.uniform(gap_lo, gap_hi)
                log(f"休息 {wait:.0f}s 再发下一条…")
                page.wait_for_timeout(int(wait * 1000))
            try:
                ok = post_one(page, j, a.publish)
            except SystemExit as e:
                log("  ✗", e); ok = False
            except Exception as e:
                log("  ✗ 异常：", repr(e)[:300]); ok = False
            results.append((j, ok))
            if not ok and a.publish:
                log("出错即停，后面的不发，避免连环出错。")
                break
        log("==== 汇总 ====")
        for j, ok in results:
            log("✓" if ok else "✗", j["time"], j["desc"])
        if not a.publish and a.hold > 0:
            log(f"浏览器保留 {a.hold} 秒供查看（可直接手动点发表）…")
            page.wait_for_timeout(a.hold * 1000)
        browser.close()
    chrome.terminate()
    kill_profile_chrome()
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    main()
