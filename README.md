# 视频号定时发布 · WeChat Channels Publisher

> 把本地视频批量传到微信视频号助手，设成定时发表。全程自动，只在需要扫码时找你。

![platform](https://img.shields.io/badge/platform-Windows-0078D4)
![python](https://img.shields.io/badge/python-3.9%2B-3776AB)
![license](https://img.shields.io/badge/license-MIT-2E7D32)

用 Playwright 驱动一个独立的 Chrome 配置，模拟真人操作视频号创作后台：上传视频 → 填描述和短标题 → 位置设成「不显示位置」→ 声明原创 → 设好定时 → **回读校验全部通过才点发表** → 到内容列表确认「将于…发表」。

因为是**站内定时**，到点由平台服务器自动发出，**你的电脑不用开着**。

## 特点

| 能力 | 说明 |
|---|---|
| **全自动上传** | 走 CDP 的 `set_input_files`，穿透 wujie 微前端的 shadow DOM，一行代码把视频交给网页。[原理](references/upload.md) |
| **不误发** | 填完先回读六项（描述/短标题/位置/时间/原创/按钮状态）逐项比对，任一项不符就停下，绝不点发表 |
| **不重复发** | 发布记录 `sph-log.jsonl` 做去重，中断重跑不会把已发的再发一遍 |
| **增量排期** | 随时把新视频丢进目录再跑一次，正在排的计划时间不会被重排 |
| **确认才发** | 计划有指纹签名，没经过 `--confirm` 或被改过一个字，`--publish` 直接拒绝（退出码 1，连浏览器都不开） |
| **扫码可续** | 登录态存在独立 Chrome 配置里，多数时候不用扫；过期了会停在扫码页明确提示，扫完自动继续 |
| **发表前留证** | 每条都存一张表单截图，事后可回溯 |

一条视频约 15–30 秒（不含上传时长）。

## 为什么不用 RPA 或浏览器插件

这个页面上试过五条路，只有 Playwright 走通：

| 通道 | 结果 | 原因 |
|---|---|---|
| **`set_input_files`** | ✅ | CDP 让浏览器内核亲自挂文件；选择器能穿透 open shadow DOM |
| 浏览器插件 / 坐标点击 | ❌ | 表单在 shadow root 里，命中检测误判 `target_covered`；且没有文件上传能力 |
| 剪贴板 `Ctrl+V` | ❌ | 页面能收到 `clipboardData.files`，但视频号上传区没监听 paste |
| 合成点击上传区 | ❌ | CDP 派的是 `isTrusted: false` 事件，Chrome 拒绝为它弹系统文件对话框 |
| `WM_DROPFILES` / 驱动系统对话框 | ❌ | Chrome 用 OLE 拖放；PowerShell 的 `Add-Type`、COM 被安全策略拦死 |

一句话：别的通道在「模拟人的操作」，`set_input_files` 是「替浏览器做它本来就会做的事」。

## 环境要求

- **Windows**（`start_chrome()` 用了 `%ProgramFiles%` 和 PowerShell；macOS / Linux 需自行改这两个函数）
- **Python 3.9+**，并装 playwright：`pip install playwright`
  **不需要** `playwright install chromium` —— 脚本直接调本机 Chrome，只为拿 CDP 端口
- **Google Chrome**（`C:\Program Files\Google\Chrome\Application\chrome.exe` 等三个常见位置任一）

## 快速开始

```bash
git clone https://github.com/newbiefly/wechat-channels-publisher.git
cd wechat-channels-publisher/scripts
pip install playwright
cp config.example.json config.json    # 改成你的数据目录
```

### 1. 登录（首次必做）

```bash
python login.py
```

弹出独立 Chrome 窗口，用**能管理这个视频号的那个微信**扫码，**扫完还要在手机上点一次「确认登录」**。

脚本会等 40 秒让 cookie 落盘 → 用 `Browser.close` 正常关机 → **重启复核**一次。复核通过才算成功，登录态存进 `sph-profile/`，之后多数时候不用再扫。

### 2. 排期（只排期，不发布）

```bash
python make_plan.py --dir "D:\videos\我的视频" --per-day 1
```

默认从明天起每天 1 条，时间落在 07:30–08:30 随机。已发过的自动跳过，输出一张「时间 | 星期 | 标题」表。

### 3. 过目 → 签字

看完那张表，确认没问题：

```bash
python make_plan.py --confirm      # 记下指纹
```

### 4. 发布

```bash
# 先演练：填完、截图、停住，不点发表
python sph_post.py --batch "<数据目录>\tmp\sph\plan.json"

# 真发
python sph_post.py --batch "<数据目录>\tmp\sph\plan.json" --publish
```

### 5. 对账

```bash
python list_scheduled.py
```

只读打开内容列表，列出平台上所有「将于…发表」的作品，并和发布记录对比，能揪出「记录有、平台没有（可能被下架）」和「平台有、记录没有（手动发过）」两种不一致。退出码 2 表示登录态过期。

## 命令速查

| 脚本 | 作用 |
|---|---|
| `login.py` | 登录 / 续期，带落盘复核 |
| `make_plan.py` | 增量排期、`--confirm` 签字、`--show` 查看 |
| `sph_post.py` | 单条或批量发布；不带 `--publish` 是演练 |
| `list_scheduled.py` | 对账平台定时作品 |
| `check_video.py` | 独立的 ffprobe 预检（大小/时长/编码），**未接入主流程**，可手动跑 |

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--desc` | 文件名（自动去 `_v2` / `-v3` / `_副本` 等版本后缀） | 视频描述 |
| `--title` | `config.json` 的 `default_short_title` | 短标题，平台限 16 字 |
| `--original` / `--no-original` | 声明原创 | 会走原创权益协议弹框 |
| `--publish` | 关 | 不带就只填不发 |
| `--per-day` | 1 | 每天最多几条 |
| `--window` | `07:30-08:30` | 每天随机时间段 |
| `--gap` | `8-12` | 批量两条之间随机间隔秒 |
| `--login-wait` | 300 | 等扫码秒数 |

完整参数见 `python <脚本> --help`。

## 安全设计

三道闸门，都不是靠人自觉：

**① 指纹签名** —— `plan.json` 的 sha1 指纹记在 `plan.meta.json`。发布前重算比对，不符就 `退出码 1`：

```
✗ 拒绝发布：这份计划还没确认过（没有 plan.meta.json）
✗ 拒绝发布：计划在你确认之后被改过（确认 19 条 / 现在 20 条，指纹 a27fd7d3 → cc4fadaf）
```

**② 回读校验** —— 填完不直接发，先把六项读回来逐项比对。任何一项不符就 `return False`，不点发表。

**③ 发布记录** —— 每条成功发布写进 `sph-publish-log.jsonl`。重跑时同名或同路径的视频直接跳过。

## 常见问题

**登录态多久失效？**
不一定，实测有两天、也有两小时的。所以每次发布前先跑 `login.py` 探一次：已登录 3 秒返回，未登录会立刻提示，不会白等。

**二维码显示「加载失败，点击重试」？**
在页面上点一下刷新再扫。**不要重跑脚本**——重跑会换窗口，等于白扫一次。

**扫了码但没进去？**
微信扫码后还要在手机上点一次「确认登录」，这一步最常被漏。

**页面白屏？**
登录后首屏经常白屏，刷新一次才渲染。脚本的 `open_clean_form()` 自带重试。

**位置怎么不是我选的？**
平台会默认带上上次用过的城市。脚本每次都显式选「不显示位置」，否则等于替你挂了个错地址。

**发表时间点不动？**
没上传视频时，底部三个按钮全灰、时间框点了没反应。所有「点了没反应」的问题，先确认视频传完没有。

更多故障处理见 [references/troubleshooting.md](references/troubleshooting.md)。

## 目录结构

```
.
├─ SKILL.md                  主流程 + 铁律 + 登录闸门操作手册
├─ references/
│  ├─ architecture.md        架构、三条硬机制、单条发布内部步骤
│  ├─ upload.md              文件上传原理（CDP 注入 vs 模拟操作）
│  ├─ login-gate.md          登录判定证据表、提示话术、轮询节奏
│  ├─ selectors.md           shadow DOM 机制 + 20 个控件真实选择器
│  └─ troubleshooting.md     端口占用、时间不可选、校验不过…
└─ scripts/
   ├─ sph_post.py            ★ 主执行器
   ├─ make_plan.py           增量排期
   ├─ sph_plan.py            计划读写 + 指纹签名
   ├─ login.py               登录 / 续期 + 落盘复核
   ├─ list_scheduled.py      对账
   ├─ sph_env.py             数据目录解析 + 状态机
   ├─ sph_log.py             发布记录
   ├─ check_video.py         ffprobe 预检（独立）
   └─ config.example.json    配置模板
```

### 数据目录

不在仓库里，由 `config.json` 的 `data_dir` 指定：

```
<data_dir>/
├─ sph-profile/           独立 Chrome 配置，含登录态（别分享、别提交）
├─ sph-publish-log.jsonl  发布记录，去重依据
└─ tmp/sph/
   ├─ plan.json           当前计划
   ├─ plan.meta.json      确认签名
   ├─ status.json         运行状态
   └─ shots/              每条发表前的表单截图
```

## 作为 AI Agent 技能使用

`SKILL.md` 是按 WorkBuddy / Claude Code 的技能格式写的（YAML frontmatter + 流程手册），把整个目录放进 `~/.workbuddy/skills/` 或 `~/.claude/skills/` 就能让 Agent 直接调用，不必记命令行。不用 Agent 的话，它就是普通 Python 脚本，`README` 里的命令可直接跑。

## 已知限制

- **仅 Windows**。
- **单账号串行**，不支持并发（并发更容易触发风控，也不符合“每天 1–2 条”的正常节奏）。
- **视频号没有面向个人创作者的官方 API**，只能走网页端，页面改版可能需要修选择器（见 [selectors.md](references/selectors.md) 第四节的排查方法）。
- 平台上**手动改过**的定时作品，脚本不知道；改完要同步删掉 `sph-publish-log.jsonl` 里对应的行，否则那天会被认为已占用。
- 平台的风控策略会变，脚本只做「拟人节奏」，**不绕过验证码**。

## 免责声明

本项目仅用于自动化管理**你自己拥有管理权限的**视频号账号。使用前请自行确认符合[微信视频号运营规范](https://channels.weixin.qq.com/)及相关服务条款。因使用本工具导致的账号异常、内容误发、数据丢失等后果由使用者自行承担。请勿用于批量养号、刷量或任何违规用途。

## License

[MIT](LICENSE)
