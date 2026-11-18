# 架构

## 主链路

```
① 增量排期  make_plan.py --dir …     → 只给新视频排空档，老计划不动
② 计划过目  把「时间 | 标题」表给人看  → ★ 必须停下等明确同意
③ 签字      make_plan.py --confirm   → 记下计划指纹
④ 发布      sph_post.py --publish    → 指纹不符直接拒绝执行
⑤ 对账      list_scheduled.py        → 核对平台「将于…发表」
```

②③ 是硬闸门，不是礼仪：`--publish` 会重算 `plan.json` 的指纹并与 `plan.meta.json` 比对，未经 `--confirm` 或被改过一个字，一律拒绝发布（退出码 1，连浏览器都不打开）。

## 分五层，各管一件事

| 层 | 文件 | 职责 |
|---|---|---|
| 规范层 | `SKILL.md` | 给 AI / 人看的主流程、铁律、登录话术、验收清单 |
| 计划层 | `make_plan.py`、`sph_plan.py` | 决定发什么、什么时候发；指纹签名 |
| 执行层 | `sph_post.py` | 真正操作浏览器（上传 → 填表 → 定时 → 校验 → 发表 → 确认） |
| 状态层 | `sph_env.py`、`sph_log.py` | 运行状态机 + 发布记录（去重依据） |
| 知识层 | `references/` | 选择器、登录判定、上传原理、故障处理 |

层与层之间靠**文件**解耦，脚本不互相调用长流程（唯一例外：`login.py`、`list_scheduled.py` 复用 `sph_post` 的浏览器启动与登录判定）。这样任何一步都能单独重跑，状态不丢。

## 三条硬机制

### 1. 指纹闸门：把“人确认”变成技术约束

```
make_plan 生成 → 清空签名 → 人过目 → --confirm 记下 sha1 指纹
                                          ↓
      sph_post --publish → 重算指纹比对 → 不符：退出码 1，浏览器都不开
```

`digest = sha1(所有 (视频名, 时间) 排序后拼接)[:16]`。实测拦截三种情况：没确认、确认后偷加一条、确认且无改动则放行。

`--force` 是唯一的绕过口，只留给“确认过但要重发已发过的视频”。

### 2. 增量排期：老计划绝不会被重排

| 视频状态 | 处理 |
|---|---|
| 已发布（同路径或同文件名都算） | 跳过 |
| 已排期未发 | 时间原样保留，标「沿用」 |
| 全新的 | 只占新的空档，标「新增」 |

所以随时可以把新视频丢进目录再跑一次，正在排的时间不会动。想推倒重排用 `--reset`。

### 3. 登录状态机：唯一必须人工介入的环节

```
need_login → running → filled → published
                └→ failed
```

写进 `<data_dir>/tmp/sph/status.json`，同时往 stdout 打 `[NEED_LOGIN]` 并让进程停住等扫码。上层不必解析中文输出就能判断要不要提示用户。

`login.py` 里钉死了两个实测坑：

- Chrome 对 cookie 写盘有几十秒批处理延迟，**必须用 CDP `Browser.close` 正常关机**，`terminate()` 硬杀会让登录态只活在内存里，下次启动照样是扫码页；
- 关掉后**重启复核**一次才算成功，否则会出现“本次看着成功、下次还要扫码”。

## 单条发布的内部步骤

```
clear_residue      清掉平台恢复的上一条上传任务
upload             set_input_files + 轮询「封面＋删除＋无百分比」
fill_desc          contenteditable，Ctrl+A Delete 再输入
fill_title         短标题，限 16 字
no_location        强制「不显示位置」（默认会带上上次的城市）
set_time           开「定时」→ 点 readonly 时间框 → 选日期时间
declare_original   勾原创 + 过权益协议弹框
readback           回读六项：描述/短标题/位置/时间/原创/发表按钮可点
check              逐项比对，任一不符就停下（不点发表）
publish            点发表 → verify_in_list 确认「将于…发表」→ 写记录
```

中间穿插 `human_click` / `human_type` 的拟人节奏，批量条目之间 `--gap 8-12` 秒随机。

## 数据目录

```
<data_dir>/
├─ sph-profile/           独立 Chrome 配置，含登录态（不要分享、不要提交）
├─ sph-publish-log.jsonl  发布记录，去重唯一依据
└─ tmp/sph/
   ├─ plan.json           当前计划
   ├─ plan.meta.json      确认签名（删掉 = 退回未确认）
   ├─ status.json         运行状态
   └─ shots/              每条发表前的表单截图
```
