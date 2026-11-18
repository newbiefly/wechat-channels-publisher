# 故障速查

## 一、启动阶段

**`ModuleNotFoundError: No module named 'playwright'`**
用错解释器了。机器上装了多个 Python 时，`pip install playwright` 装到 A、运行时用的是 B。
先确认：`python -c "import playwright; print(playwright.__file__)"`，再换同一个解释器跑脚本。

**`Target page, context or browser has been closed` / 浏览器弹出来马上关掉**
1. 上一个独立 Chrome 还占着配置目录 —— 脚本启动前会自动杀，没杀干净就手动看一眼有没有残留在跑。
2. **9333 端口被占**。查：`netstat -ano | findstr 9333`，占用的进程不是 chrome 就换端口（改 `sph_post.CDP_PORT`）。
3. 极少数情况是 Chrome 版本太新，`--remote-debugging-port` 需要配 `--user-data-dir`（脚本已经带了）。

**`Chrome 调试端口没起来`**
chrome.exe 路径找不到。确认 `C:\Program Files\Google\Chrome\Application\chrome.exe` 存在；装在别处就改 `CHROME_CANDIDATES`。

**卡在 `[NEED_LOGIN]` 不动**
登录闸门，正常现象。见 `login-gate.md`。

## 二、上传阶段

**上传超时（默认 180 秒，脚本会自动刷新重试一次）**
- 视频太大或网络慢。15MB 约 11 秒；100MB 要 1–2 分钟。可以调 `upload(page, video, timeout=...)`。
- 页面残留了上一条。脚本的 `open_clean_form()` 会清理；清不掉会报「发表页残留清不掉」。
- 格式不支持：视频号要 mp4（H.264 + AAC）。用 `check_video.py` 先校验：

  ```bash
  py -3 check_video.py "D:\...\视频.mp4" --at "2026-11-25 08:00"
  ```

  输出 `{"ok": false}` 就别传，先转码。

**上传失败：不支持该格式**
同上，转码：`ffmpeg -i in.mov -c:v libx264 -pix_fmt yuv420p -c:a aac out.mp4`。

## 三、填表阶段

**这一天不可选**
时间早于当前，或超出平台允许的定时范围（平台只允许往后一段时间）。换近一点的时间。

**时间不可选（需晚于当前时间）**
选的钟点已经过了。注意：**日期是今天时，小时必须大于当前小时**。

**短标题超过 16 字**
清单里的 `title` 改短，或不写（默认取 `config.json` 的 `default_short_title`）。

**位置没改成「不显示位置」**
下拉没展开就点了。`set_time`/`no_location` 里都有 `.filter(visible=True)` + `wait_for(visible)`，改版后优先检查这两处。

**原创协议框超时**
页面里有两份同样的弹框，隐藏那份先被匹配到了。确认 `.filter(visible=True)` 还在。

## 四、校验与发表

**`✗ 校验未通过，不发表：...`**
脚本按回读结果逐项对比，列出不一致项。先看截图 `<data_dir>/tmp/sph/shots/<描述>.png`，再按不一致项查：
- `描述不一致` → contenteditable 没清干净（`Ctrl+A` + `Delete` 失效，可能焦点没进去）
- `位置：xxx` → 下拉没选上
- `定时：` 空 → 面板没选完，或最后没点「发表时间」标签收起面板
- `还有弹框没关` → 原创弹框没确认成功
- `页面提示：[...]` → 平台自己报的错，按文案处理
- `发表按钮不可点` → **视频还在转码**，再等等（脚本最多等 10 分钟）

**点了发表但没跳到内容列表**
看 `shots/<描述>_after.png`。常见原因：二次确认弹窗、风控挑战、网络断了。这一条**不写进发布记录**，可以重跑。若截图显示其实已经发出去了，去内容管理确认后再决定是否 `--force`。

**`? 已跳到内容列表但没找到这条的定时记录`**
已按「可能已发」写进记录（`verified: false`），防止重跑重复发。**必须人工去内容管理确认**，然后：
- 确实发出去了 → 什么都不用做
- 其实没发出去 → 删掉记录里那行，再单独重跑这条

## 五、对账

**`平台上有、记录里没有`**
有人在网页上手动发过，或者记录被删了。不影响运行，只是去重会失效。

**`记录里有、平台上没找到`**
1. 先确认**不是登录过期**（退出码 2 就是登录问题，不是数据问题）
2. 审核未过 / 被下架 → 去内容管理看
3. 已经在平台上手动删了定时 → **必须把 `sph-publish-log.jsonl` 对应那行删掉**，否则这个视频和那一天永远算作已占用

**`⚠ 平台上重复 n 条`**
同一天同一时间撞了。去内容管理删掉多余的，再删记录里对应的行。

## 六、运行方式相关

**命令跑很久没输出**
已经用 `run_in_background` 跑了吗？发布一条 30 秒，批量十几条要几分钟，前台等必超时。

**后台任务看起来"卡住"**
正常——它在等上传/转码/扫码。**先读 `status.json` 判断阶段**，别急着杀进程：

```bash
py -3 -c "import sys;sys.path.insert(0,'.');import sph_env,json;print(json.dumps(sph_env.read_status(),ensure_ascii=False))"
```

`state` 的取值：`need_login`（等扫码）→ `running`（传/填中）→ `filled`（演练结束）→ `published`（成功）/ `failed`（停了，看 `reason`）。

**要不要杀进程重启**
只在确认卡死（比如 `running` 超过 15 分钟不动）时才杀。注意杀之前记下当前发到哪一条，重启后要 `--force` 才能重发那一条，或者等它自己超时退出。

**Chrome 窗口找不到**
这个 Chrome 用的是独立配置（`sph-profile`），任务栏图标和日常 Chrome 一样。按 Alt+Tab 找"视频号助手"标签，或在任务管理器里看命令行含 `sph-profile` 的进程。
