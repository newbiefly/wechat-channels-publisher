# 文件上传是怎么做到的

整个方案能成立就靠这一环。写清楚原理，页面改版时才知道该动哪里、不该动哪里。

## 一、网页侧的正常机制：只让人类填

网页拿本地文件只有一个入口：

```html
<input type="file" accept="video/*">
```

浏览器故意把它做成“只有人能填”：

- **网页 JS 不能设置 `input.value`**（否则任意网站都能静默读取你硬盘上的文件）；
- 用户通过系统对话框选完后，浏览器把文件封装成 `File` 对象放进 `input.files`（一个 `FileList`）；
- 浏览器替页面派发 `input` 和 `change` 事件，页面监听 `change`，从 `input.files[0]` 拿走。

正常链路：

```
点上传区 → 系统对话框 → 选文件 → 浏览器构造 File → 派发 change → 页面 JS 拿走
```

`File` 里装的是**文件引用**（浏览器进程持有），不是文件内容，所以几百 MB 也瞬间完成；真正的字节在上传时才流式读取。

## 二、自动化侧怎么绕开：CDP 不是 JS

Playwright 没有“模拟点击”，它走 Chrome DevTools Protocol：

```
locator.set_input_files("D:/videos/a.mp4")
        ↓  Playwright 解析选择器，拿到元素的 backendNodeId
CDP: DOM.setFileInputFiles({ files: ["D:/videos/a.mp4"], backendNodeId })
        ↓  Chrome 浏览器进程亲自打开文件、构造 File、挂到该 input 的 files 上
        ↓  替页面派发 input + change
```

关键在于**权限层级**：CDP 是浏览器自己的调试协议，站在渲染进程之上。网页 JS 被禁止的事，浏览器内核自己做是合法的。`set_input_files` 扮演的不是“脚本”，而是“浏览器”。

```
自动化侧（Playwright + CDP）
  本地 mp4 ──→ DOM.setFileInputFiles ──→ input.files（File 对象）
                                              │ 派发 change
网页侧（页面自己的事）                          ↓
  封面预览 + 删除按钮 ←── 分片上传·转码 ←── 读 files[0]
```

文件挂进 `input` 之后，上传、转码、进度**全是页面自己干的**，脚本不再参与，只是观察页面状态。

## 三、落到代码里

`sph_post.py` 的 `upload()` 只有一行干活：

```python
page.locator("input[type=file][accept*=video]").first.set_input_files(video)
```

后面全是等。三个要点：

**① 不用点“上传”按钮。** 这个 `input` 一开始就在 DOM 里，只是被 CSS 藏起来了。`set_input_files` 不看元素是否可见、是否可点，直接按 nodeId 挂文件。

**② 选择器能穿透 shadow DOM。** 视频号是 wujie 微前端，表单在 `wujie-app` 的 **open** shadow root 里。Playwright 的选择器解析由它自己做（维护自己的 DOM 快照并穿透 open shadow root），`input[type=file]` 直接命中，不需要 `evaluate` 进去手动找。

**③ 完成判据是三个条件，不是一个：**

```javascript
ok = 有「封面预览」 && 有「删除」按钮 && 文本里没有 "N%"
```

因为**封面出现 ≠ 传完**。封面是浏览器本地解码抽帧，秒出；上传和转码在后面两段。真正的“好了”是转码完成、平台回填 video id、底部按钮解锁，此时才同时出现「删除」且进度百分比消失。

## 四、五条通道的实测结果

| 通道 | 结果 | 机制上差在哪 |
|---|---|---|
| **`set_input_files`** | ✅ | 落在浏览器内核层，合法且干净 |
| 剪贴板 `Ctrl+V` | ❌ | 机制本身通（页面 `paste` 能拿到 `clipboardData.files`，用测试页验证过），但**视频号上传区没监听 paste** |
| 合成点击上传区 | ❌ | CDP 派的是 `isTrusted: false` 的合成事件，Chrome 拒绝为它打开系统文件对话框 |
| `WM_DROPFILES` 投递 | ❌ | Chrome 用 **OLE 拖放**，不认这个 Windows 消息 |
| 驱动系统“打开”对话框 | ❌ | PowerShell 的 `Add-Type` / `Assembly.Load` / COM 全被安全策略拦死 |

一句话：**别的通道都在模拟人的操作，`set_input_files` 是替浏览器做它本来就会做的事。**

## 五、两个常见误解

| 误解 | 实际 |
|---|---|
| “它把文件内容塞进去” | 只塞**引用**。真正的字节是页面 fetch 时由 Chrome 流式读的，所以几百 MB 也瞬间完成，耗时全在后面的网络传输 |
| “必须给文件路径” | 也能给内存数据：`set_input_files([{"name":"a.mp4","mimeType":"video/mp4","buffer":b}])`，适合先在内存里处理好再传 |

还有一种用法：如果 `input` 是点击后才动态创建的（有些站这样），得先 `page.expect_file_chooser()`，再点按钮，再 `chooser.set_files(...)`。视频号是静态 `input`，用不上这套。
