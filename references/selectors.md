# 页面结构与选择器（实测）

## 一、最关键的一件事：表单在 open shadow DOM 里

视频号助手是 **wujie 微前端**，发表页的整个表单挂在 `<wujie-app>` 的 shadowRoot 内：

```js
document.querySelector('wujie-app').shadowRoot   // 表单都在这里面
```

- 它是 **open** shadow DOM，所以 **Playwright 的 CSS 选择器能直接穿透**，`page.locator(".input-editor")` 就命中 shadowRoot 里的元素，不需要手动 `evaluate` 进去找。
- 只有需要**批量读状态**（一次拿描述、标题、位置、时间、原创、按钮是否可点）时才用 `page.evaluate` 手动 `querySelector('wujie-app').shadowRoot`。见 `sph_post.py` 的 `readback()`。
- 这也正是浏览器插件方案失败的根本原因：坐标点击拿不到这个层级，上传也进不去。

## 二、控件对照表

| 控件 | 选择器 | 类型 | 备注 |
|---|---|---|---|
| 视频文件框 | `input[type=file][accept*=video]` | file | `set_input_files(路径)` 一次传完，**这是全自动的关键** |
| 上传完成标志 | `.post-upload-wrap` 内有「封面预览」+「删除」按钮且无 `N%` | 断言 | 只出现封面不等于传完，**按钮可点才算完** |
| 视频描述 | `.input-editor` | contenteditable | 点进去 `Ctrl+A` `Delete` 再输入；输完点「视频描述」标签收起话题联想 |
| 短标题 | `input[placeholder^='填写短标题']` | input | 限 16 字，超了字段下方报错 |
| 位置 | `.post-position-wrap .position-display` → `.option-item .name` | 下拉 | **默认带一个城市**，必须选「不显示位置」 |
| 定时开关 | `label.weui-desktop-form__check-label` 文案「定时」 | radio | 单选：不定时 / 定时 |
| 发表时间 | `dl.weui-desktop-picker__date-time input[placeholder='请选择发表时间']` | readonly | 不能直接敲，必须先点开面板 |
| 日期面板 | `.weui-desktop-picker__panel_day` | 面板 | 年月标签在 `.weui-desktop-picker__panel__label`（[0]=年 [1]=月） |
| 翻月 | `.weui-desktop-btn__icon__left` / `__right` | 按钮 | 用年月标签比对目标，别按固定次数翻 |
| 日期格 | `table a:not(.weui-desktop-picker__faded)` 文案为日 | 链接 | `class` 含 `disabled` = 这天不可选 |
| 时间面板 | `dl.weui-desktop-picker__time` | 面板 | 时：`ol.weui-desktop-picker__time__hour li`；分：`...time__minute li` |
| 声明原创 | `.form-item` 内含 `.label` 文案「声明原创」→ `input[type=checkbox]` | 勾选 | 勾了会弹「原创权益」协议框 |
| 原创协议框 | `.weui-desktop-dialog` 的标题为「原创权益」且可见 | 弹框 | **页面里有两份（一份隐藏）**，必须 `.filter(visible=True)` |
| 协议勾选 | `.original-proto-wrapper input[type=checkbox]` | 勾选 | 不勾时「声明原创」按钮是 disabled |
| 协议确认 | 该弹框 `.weui-desktop-dialog__ft button` 文案「声明原创」 | 按钮 | 等 `disabled` 消失再点 |
| 发表 | `button` 文案精确等于「发表」且可见，取 `.last` | 按钮 | 视频没传完/转码中时是灰的 |
| 保存草稿 / 手机预览 | 同上的按钮 | 按钮 | 演练时浏览器保留窗口，可人工点 |

内容列表（`/platform/post/list`）同样在 `wujie-app` 的 shadowRoot 里，读全文用：

```js
const R = document.querySelector('wujie-app')?.shadowRoot;
(R ? [...R.children].map(e => e.innerText).join('\n') : document.body.innerText)
```

## 三、页面上的几个坑（都踩过）

1. **登录后首屏经常白屏**，要刷新一次才渲染。脚本用 `open_clean_form()` 反复 `goto` + 轮询，等于自带重试。
2. **上一条的视频会残留**：打完一条立刻再打开发表页，页面还挂着上一条（封面预览 + 「取消上传」+ 0% 进度条）。脚本先检测再点「取消上传」/「删除」，清不掉就停下人工处理。
3. **位置默认不是空的**，是上次用过的城市。不显式选「不显示位置」就是替用户挂了错地址。
4. **「发表时间」是 readonly**，`fill()` 无效，只能点面板。
5. **没上传视频时**，底部三个按钮全灰、时间框点了没反应——所有「点了没反应」的问题，先确认视频传完没有。
6. **封面出来 ≠ 传完**：上传和转码是两段。`readback()["publishEnabled"]` 变 true 才是真的好了（脚本为此最多等 10 分钟）。
7. **弹框有两份 DOM**（一份隐藏），任何 `has=...` 定位都要 `.filter(visible=True)`，否则会点到隐藏那份然后超时。

## 四、选择器失效怎么修

平台改版后按这个顺序查，别猜：

```bash
# 1. 先把表单结构 dump 出来（在 scripts 目录下，会开一个 Chrome）
py -3 -c "
import sph_post
from playwright.sync_api import sync_playwright
sph_post.kill_profile_chrome(); ch = sph_post.start_chrome()
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(f'http://127.0.0.1:{sph_post.CDP_PORT}')
    pg = b.contexts[0].pages[0]
    sph_post.ensure_login(pg)
    sph_post.open_clean_form(pg)
    print(pg.evaluate('''() => { const R = document.querySelector(\"wujie-app\").shadowRoot;
      return [...R.querySelectorAll(\"input,button,textarea,label,dl,ol\")]
        .map(e => [e.tagName, e.type||\"\", e.className.toString().slice(0,60),
                   (e.placeholder||e.textContent||\"\").trim().slice(0,20)].join(\" | \")).join(\"\\n\"); }'''))
    b.close()
sph_post.kill_profile_chrome()"
```

拿到真实标签后，只改 `sph_post.py` 里对应的 locator，不要改流程；改完先跑不带 `--publish` 的演练，看回读的 JSON 是否和预期一致。
