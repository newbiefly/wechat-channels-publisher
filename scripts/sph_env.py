# -*- coding: utf-8 -*-
"""数据目录与运行状态。

早期版本靠目录层级 parents[4] 反推工作区，换个位置就失效，改成显式解析：

  SPH_DATA_DIR 环境变量  >  scripts/config.json 的 data_dir  >  当前工作区的 .agents

状态文件（供上层轮询，判断是否需要用户扫码）：
  <DATA>/tmp/sph/status.json   {"state": "...", "at": "...", 其他字段}
"""
import json, os
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
CONFIG = SCRIPTS / "config.json"

# 状态取值
NEED_LOGIN = "need_login"      # 停在扫码页，等用户用微信扫码 + 手机上确认登录
RUNNING = "running"            # 已登录，正在传/填表
FILLED = "filled"              # 表单填完、回读校验通过，未点发表（演练模式到此为止）
PUBLISHED = "published"        # 已点发表，内容列表已确认
FAILED = "failed"              # 出错停下


def config() -> dict:
    """读 scripts/config.json。缺文件/坏文件都返回空 dict，不抛异常。"""
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get(key, default=None):
    """取配置项，缺省用 default。"""
    return config().get(key, default)


def data_root() -> Path:
    env = os.environ.get("SPH_DATA_DIR")
    if env:
        return Path(os.path.expandvars(env)).expanduser().resolve()
    d = get("data_dir")
    if d:
        return Path(os.path.expandvars(d)).expanduser().resolve()
    return (Path.cwd() / ".agents").resolve()


def tmp_dir() -> Path:
    p = data_root() / "tmp" / "sph"
    p.mkdir(parents=True, exist_ok=True)
    return p


def status_path() -> Path:
    return tmp_dir() / "status.json"


def set_status(state, **extra):
    """写状态文件。上层调用方靠它判断要不要提示用户扫码。"""
    try:
        data = {"state": state, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        data.update(extra)
        status_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 状态写不进去不能影响主流程


def read_status():
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except Exception:
        return None
