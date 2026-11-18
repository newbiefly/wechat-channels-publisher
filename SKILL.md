---
name: wechat-channels-publisher
description: |
  视频号定时发表（Playwright 驱动独立 Chrome，全自动上传 + 填表 + 定时 + 校验）。
  把本地 mp4 传到微信视频号助手并设成定时发表：自动填描述、短标题、位置选「不显示位置」、声明原创，
  回读校验全部通过才点发表，再到内容列表确认「将于…发表」才算成功；有发布记录防重复发。
  登录态过期会停在扫码页并明确提示用户扫码（含手机上点确认），登录态持久化在独立 Chrome 配置里。
  触发词：视频号定时发布、视频号发布、发视频号、定时发视频号、批量发视频号、视频号排期、视频号扫码登录
version: 3.0.0
---

# 视频号定时发表

一条视频约 15–30 秒。站内定时：现在就把视频传完并设好时间，到点由平台服务器自动发出，**到点不需要开机**。

```
① 增量排期 make_plan.py --dir …      → 只给新视频排空档，老计划不动
② 把「时间 | 标题」表贴给用户过目     → ★ 必须停下等用户明确同意
③ 签字 make_plan.py --confirm        → 记下计划指纹
④ 发布 sph_post.py --batch --publish → 指纹不符直接拒绝执行
⑤ 对账 list_scheduled.py
                  每条：上传 → 描述 → 短标题 → 不显示位置 → 定时 → 声明原创
                        → 回读校验 → 点发表 → 内容列表确认 → 写发布记录
```

**②③ 不是礼仪，是硬闸门**：`--publish` 会重算计划指纹并与 `plan.meta.json` 比对，未经 `--confirm` 或被改过一个字，一律拒绝发布。

## 前置条件（四条，缺一不可）

1. **Windows**。`start_chrome()` / `kill_profile_chrome()` 用了 `%ProgramFiles%` 和 PowerShell；macOS / Linux 要自己改这两个函数。
2. **Python 3.9+ 且已装 playwright**：`pip install playwright`。
   **不需要** `playwright install chromium` —— 脚本直接调本机 Chrome，只为拿到 CDP 端口。
   ⚠ 多个 Python 共存时容易装错解释器，先 `python -c "import playwright"` 确认一遍。
3. **装了 Google Chrome**（`C:\Program Files\Google\Chrome\Application\chrome.exe` 等三个常见位置任一）。
4. **数据目录可写**。读 `scripts/config.json` 的 `data_dir`（先复制 `config.example.json` 改），也可用 `SPH_DATA_DIR` 环境变量临时覆盖。
   这个目录存登录态、发布记录、截图、计划，**跟着账号走**，别提交进仓库。

脚本目录（下文命令都 `cd` 到这里）：

```
<仓库目录>/scripts
```

## 铁律（违反就是发错内容）

1. **不带 `--publish` 只在演练**：填完、截图、停住，不点发表。要真发必须显式 `--publish`。
2. **用户没确认计划，不许发布。** 这不靠自觉：`--publish --batch` 会校验计划指纹，没 `--confirm` 或计划被改动过，脚本自己退出码 1。**绝对不要用 `--force` 绕过它**（`--force` 只用于「确认过但要重发已发过的视频」）。
3. **一次只跑一个发布进程**，同一账号串行；批量时每天 1–2 条，别贪多。
4. **校验失败绝不放行**：`check()` 任一项不过脚本自己会停，不要绕过它手动点发表。
5. 不导出、不复制 `sph-profile/`（含登录态），不把 cookie 写进任何输出。

## 第一步：确认登录态（每次发布前必做）

发布脚本 `ensure_login()` 会自己处理，但它会白等 5 分钟。**更省事的做法是先跑 login.py 探一次**：

```bash
py -3 login.py --wait 180
```

| 输出 | 含义 | 动作 |
|---|---|---|
| `✓ 登录态已保存到 ...` | 已登录 | 直接进第二步 |
| `[NEED_LOGIN] 请在打开的 Chrome 窗口用微信扫码…` | 未登录，进程**阻塞等你** | 见下方「登录闸门」 |
| `等待扫码超时` + 退出码 2 | 超时没扫 | 告诉用户「本次没有发布任何内容」，浏览器窗口保留，可随时重跑 |

## 登录闸门（用户扫码时怎么做）

这是整个技能唯一必须人工介入的环节。**出现以下任意一个信号就是需要登录**：

- 输出里出现 `[NEED_LOGIN]`
- `status.json` 的 `state == "need_login"`（在 `<data_dir>/tmp/sph/status.json`）
- 脚本退出码 2
- 页面 URL 落到 `channels.weixin.qq.com/login.html`

**必须按这个顺序处理，不许跳步：**

1. **立刻明确告知用户**，话术三段，缺一段用户就会卡住：

   > 视频号登录态过期了，已弹出一个 Chrome 窗口停在扫码页。
   > 请用**能管理这个视频号的那个微信**扫码；
   > **扫完还要在手机上点一次「确认登录」**，只扫码不确认不会进去。
   > 扫完告诉我一声，或者我这边检测到就继续（我在盯着，最多等 N 分钟）。

2. **把 Chrome 窗口带到前台**：脚本已启动窗口，但可能被其他窗口盖住。提醒用户看任务栏的 Chrome 图标（这个窗口用独立配置，标签页里是视频号扫码页）。

3. **轮询等待，不要干等也不要反复重跑**：

   ```bash
   # 每 10–15 秒看一次，别更密
   py -3 -c "import sys;sys.path.insert(0,'.');import sph_env,json;print(json.dumps(sph_env.read_status(),ensure_ascii=False))"
   ```

   - `state` 变成 `running` → 登录成功，继续。
   - 一直 `need_login` → 每 60 秒提醒一次剩余时间即可，别刷屏。
   - 超过 `--wait` 秒仍失败 → 停止，报告「发布未开始，登录态没拿到」，不要假装成功。

4. **二维码过期**：页面会出现「加载失败，点击重试」。让用户点一下刷新二维码再扫；**不要重跑脚本**（重跑会换窗口，用户白扫一次）。

5. **连续 3 次扫码都进不去**：换一个微信账号试，或者让用户手动在该 Chrome 窗口里登录后回复，再继续。

## 第二步：增量排期（只排期，不发布）

```bash
# 按目录挑全部 mp4，每天 1 条，时间落在 07:30–08:30 随机
py -3 make_plan.py --dir "D:\videos\我的视频" --per-day 1

# 按关键词挑
py -3 make_plan.py --dir "D:\videos\我的视频" --include 做PPT 接管浏览器

# 指定单条
py -3 make_plan.py "D:\...\视频A.mp4" "2026-11-25 08:00"

# 查看当前计划 + 签名状态
py -3 make_plan.py --show
```

**默认是增量模式**，随时可以把新视频丢进目录再跑一次，老计划不会被打乱：

| 视频状态 | 处理 |
|---|---|
| 已发布（发布记录里有，同路径或同文件名都算） | 不排，打印「跳过（已发过）」 |
| 已排期但还没发（在 `plan.json` 里） | **日期时间原样保留**，标记「（沿用）」 |
| 全新的 | 占用新的空档日期，标记「（新增）」 |

其他规则：
- 默认从**明天**开始，`--start 2026-12-01` 可改。
- 某天已排满 `--per-day` 条（已发布的 + 已排期的都算）就顺延到下个空日子；同一天多条至少错开 10 分钟。
- 想推倒重排用 `--reset`（会丢掉现有排期，慎用）。
- 清单写到 `<data_dir>/tmp/sph/plan.json`，同时打印「时间 | 星期 | 标题」。

## ★ 第二步半：把计划贴给用户，等他同意

**这一步不许跳过。** 把脚本打印的这张表原样（或整理成表）发给用户：

```
共 19 条（沿用 0，新增 19）
2026-11-23 07:56  一   第一个视频的标题
2026-11-24 08:09  二   第二个视频的标题
...
```

等用户明确表态（「可以」「发吧」「确认」）。用户要改内容/改时间/删几条，**先改再重新生成或手改 `plan.json`**，然后重新过目。

用户同意后才签字：

```bash
py -3 make_plan.py --confirm     # 打印指纹，例如 a27fd7d32216b32c
```

签完字再改 `plan.json`，指纹就对不上，发布会自动被拒——这是设计如此，不是故障。要改就重新走一遍「生成 → 过目 → confirm」。

## 第三步：发布

```bash
# 演练（强烈建议先跑一次）
py -3 sph_post.py --batch "<数据目录>\tmp\sph\plan.json"

# 真发
py -3 sph_post.py --batch "<数据目录>\tmp\sph\plan.json" --publish

# 单条
py -3 sph_post.py "D:\videos\a.mp4" "2026-11-25 08:00" --publish
```

`--publish --batch` 会**先校验计划签名，不过就直接退出码 1，连浏览器都不打开**：

| 输出 | 含义 | 怎么办 |
|---|---|---|
| `✗ 拒绝发布：这份计划还没确认过（没有 plan.meta.json）` | 没走第二步半 | 贴表给用户 → `make_plan.py --confirm` |
| `✗ 拒绝发布：计划在你确认之后被改过（确认 19 条 / 现在 20 条…）` | 签名后计划变了 | 重新过目 → 重新 confirm |
| `计划校验通过：已确认（19 条，2026-11-18 12:28:37）` | 正常 | 继续 |

执行要点：

- **用后台任务跑**，别在前台干等（一条 30 秒，批量十几条要几分钟）。命令超时设 600 秒以上。
- 跑起来后**先等 20–30 秒**再读一次输出，确认没卡在 `[NEED_LOGIN]`，也没报 `Target page, context or browser has been closed`。
- 批量时一条失败就停，后面的不发——这是脚本的设计，不要手动重跑整批（会把已发的重发）。
- 每条都会存一张发表前的表单截图：`<data_dir>/tmp/sph/shots/<描述>.png`，出问题先看它。

## 第四步：对账

```bash
py -3 list_scheduled.py
```

只读打开内容列表，列出平台上所有「将于…发表」的作品，并和发布记录对比，三处输出：

- `记录里有、平台上没找到` → 可能被平台下架/审核未过，需要人工看内容管理
- `平台上有、记录里没有` → 有人在网页上手动发过，记录不全
- `⚠ 平台上重复 n 条` → 同一时间撞了，去删掉多余的

退出码 2 表示登录态过期（不是「0 条定时」），先跑 `login.py`。

## 参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--desc` | 视频文件名（去扩展名、去 `_v2` / `-v3` / `_副本` 等版本后缀） | 视频描述 |
| `--title` | `config.json` 的 `default_short_title`（缺省「日常分享」） | 短标题，平台限 16 字，超了直接报错 |
| `--original` / `--no-original` | 声明原创 | 会走「原创权益」协议确认弹框 |
| `--publish` | 关 | 不带就只填不发 |
| `--force` | 关 | 忽略发布记录，强行再发（慎用） |
| `--per-day` | 1 | 每天最多几条 |
| `--login-wait` | 300 | 等扫码秒数 |
| `--hold` | 600 | 不发表时浏览器保留秒数 |
| `--no-jitter` | 关 | 关闭拟人随机节奏，只调试时用 |
| `--gap` | `8-12` | 批量两条之间随机间隔秒 |

## 文件位置

| 路径 | 内容 |
|---|---|
| `<data_dir>/sph-profile/` | 独立 Chrome 配置，含视频号登录态，**不要分享、不要提交** |
| `<data_dir>/sph-publish-log.jsonl` | 发布记录，去重唯一依据。平台上手动删了某条定时，要把对应那行也删掉 |
| `<data_dir>/tmp/sph/shots/` | 每条发表前的表单截图 |
| `<data_dir>/tmp/sph/status.json` | 运行状态（`need_login` / `running` / `filled` / `published` / `failed`） |
| `<data_dir>/tmp/sph/plan.json` | 当前定时计划（增量排期的基准，别手动乱改） |
| `<data_dir>/tmp/sph/plan.meta.json` | 计划确认签名（指纹 + 确认时间）。删掉它 = 计划退回未确认状态 |

## 参考文档

- `references/login-gate.md` —— 登录判定证据表、提示话术、轮询节奏、各异常分支
- `references/selectors.md` —— wujie shadow DOM 机制、每个控件的真实选择器、失败时怎么修
- `references/troubleshooting.md` —— 端口占用、配置目录残留、时间不可选、校验不过、发表没跳转

## 为什么是 Playwright

这个页面上试过五条路，只有 Playwright 走通。记下来免得后来人重走：

| 通道 | 结果 | 原因 |
|---|---|---|
| **Playwright `set_input_files`** | ✅ | 走 CDP 的 `DOM.setFileInputFiles`，浏览器内核亲自挂文件；CSS 选择器能穿透 open shadow DOM |
| 浏览器插件类工具（坐标点击） | ❌ | 表单在 wujie 微前端的 shadow root 里，命中检测误判成 `target_covered`；且没有等价的文件上传能力 |
| 剪贴板 `Ctrl+V` | ❌ | 页面 `paste` 里能拿到 `clipboardData.files`（实测），但视频号上传区**没监听 paste** |
| 合成点击上传区 | ❌ | CDP 派的是 `isTrusted: false` 的合成事件，Chrome 拒绝为它打开系统文件对话框 |
| `WM_DROPFILES` / 驱动系统对话框 | ❌ | Chrome 用 OLE 拖放不认这个消息；PowerShell 的 `Add-Type`、COM 调用被安全策略拦死 |

核心差别：**前四条都在“模拟人的操作”，`set_input_files` 是“替浏览器做它本来就会做的事”**。前者要穿透可见层、事件层、系统层，后者直接落在内核层。

文件上传的完整原理见 `references/upload.md`。
