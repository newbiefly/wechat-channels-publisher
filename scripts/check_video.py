#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号发布前校验：视频文件 + 定时时间。

只做本地检查，不联网、不碰浏览器。

用法：
  python check_video.py "D:/videos/a.mp4"
  python check_video.py "D:/videos/a.mp4" --at "2026-11-18 09:00"

输出单行 JSON：
  {"ok": true/false, "errors": [...], "warnings": [...], "video": {...}, "schedule": {...}}
退出码：0 = 可以继续；1 = 有 error，必须停下处理。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

MAX_BYTES = 20 * 1024 * 1024 * 1024      # 页面写明 20GB
MAX_SECONDS = 8 * 3600                   # 页面写明 8 小时
MIN_SCHEDULE_AHEAD = timedelta(minutes=30)
TIME_FMT = "%Y-%m-%d %H:%M"

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
# 页面建议：H.264/H.265、码率 <= 10Mbps、帧率 <= 60fps、AAC 音频
OK_VCODEC = {"h264", "hevc"}
OK_ACODEC = {"aac"}


def find_ffprobe():
    p = shutil.which("ffprobe")
    if p:
        return p
    # Windows 上用 winget / scoop 装的 ffmpeg 不一定在 PATH 里，兜底找一下
    for guess in (os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Packages"),
                  os.path.expandvars(r"%USERPROFILE%\scoop\apps\ffmpeg\current\bin")):
        if guess and os.path.isdir(guess):
            for root, _dirs, files in os.walk(guess):
                if "ffprobe.exe" in files:
                    return os.path.join(root, "ffprobe.exe")
    return None


def probe(path):
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None, "未找到 ffprobe，跳过媒体信息检查"
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return None, "ffprobe 超时"
    if out.returncode != 0:
        return None, "ffprobe 解析失败：%s" % (out.stderr or "").strip()[:200]
    try:
        return json.loads(out.stdout), None
    except json.JSONDecodeError:
        return None, "ffprobe 输出无法解析"


def check_video(path):
    errors, warnings, info = [], [], {}
    if not os.path.isabs(path):
        errors.append("必须传绝对路径：%s" % path)
    if not os.path.isfile(path):
        errors.append("文件不存在：%s" % path)
        return errors, warnings, info

    size = os.path.getsize(path)
    info["size_bytes"] = size
    info["size_human"] = "%.1f MB" % (size / 1024 / 1024)
    if size > MAX_BYTES:
        errors.append("文件超过 20GB：%s" % info["size_human"])
    if size == 0:
        errors.append("文件是空的")

    ext = os.path.splitext(path)[1].lower()
    info["ext"] = ext
    if ext not in VIDEO_EXT:
        warnings.append("扩展名 %s 不是常见视频格式（mp4/mov/m4v）" % ext)

    data, warn = probe(path)
    if warn:
        warnings.append(warn)
    if data:
        fmt = data.get("format") or {}
        try:
            dur = float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        info["duration_seconds"] = round(dur, 1)
        info["duration_human"] = str(timedelta(seconds=int(dur)))
        if dur > MAX_SECONDS:
            errors.append("时长超过 8 小时：%s" % info["duration_human"])
        if 0 < dur < 2:
            warnings.append("视频只有 %.1f 秒，视频号可能拒收" % dur)

        vs = next((s for s in data.get("streams", [])
                   if s.get("codec_type") == "video"), None)
        as_ = next((s for s in data.get("streams", [])
                    if s.get("codec_type") == "audio"), None)
        if vs:
            info["width"] = vs.get("width")
            info["height"] = vs.get("height")
            info["vcodec"] = vs.get("codec_name")
            info["fps"] = vs.get("r_frame_rate")
            if vs.get("codec_name") not in OK_VCODEC:
                warnings.append("视频编码 %s 不在建议范围（H.264/H.265）"
                                % vs.get("codec_name"))
            w, h = vs.get("width") or 0, vs.get("height") or 0
            if w and h and w < 1080 and h < 1080:
                warnings.append("分辨率 %sx%s 低于建议的 1080p" % (w, h))
        else:
            errors.append("没有视频流，可能不是有效视频文件")
        if as_:
            info["acodec"] = as_.get("codec_name")
            if as_.get("codec_name") not in OK_ACODEC:
                warnings.append("音频编码 %s 不在建议范围（AAC）"
                                % as_.get("codec_name"))
        else:
            warnings.append("没有音频流")
    return errors, warnings, info


def check_schedule(at_str, now=None):
    now = now or datetime.now()
    errors, warnings, info = [], [], {"input": at_str, "now": now.strftime(TIME_FMT)}
    try:
        target = datetime.strptime(at_str.strip(), TIME_FMT)
    except ValueError:
        errors.append("时间格式必须是 YYYY-MM-DD HH:mm（例：2026-11-18 09:00）")
        return errors, warnings, info
    info["target"] = target.strftime(TIME_FMT)
    info["weekday"] = "一二三四五六日"[target.weekday()]

    delta = target - now
    info["delta_human"] = str(delta).split(".")[0]
    if delta <= timedelta(0):
        errors.append("定时时间已经过去了：%s" % info["target"])
    elif delta < MIN_SCHEDULE_AHEAD:
        warnings.append("距离现在不足 %d 分钟，视频号可能不允许这么近的定时"
                        % int(MIN_SCHEDULE_AHEAD.total_seconds() // 60))
    if delta > timedelta(days=30):
        warnings.append("超过 30 天，平台可能有上限，以页面提示为准")
    return errors, warnings, info


def main():
    ap = argparse.ArgumentParser(description="视频号发布前校验")
    ap.add_argument("video", help="视频文件绝对路径")
    ap.add_argument("--at", help="定时发表时间，格式 'YYYY-MM-DD HH:mm'")
    args = ap.parse_args()

    errors, warnings, vinfo = check_video(args.video)
    result = {"ok": True, "errors": errors, "warnings": warnings, "video": vinfo}
    if args.at:
        serr, swarn, sinfo = check_schedule(args.at)
        errors.extend(serr)
        warnings.extend(swarn)
        result["schedule"] = sinfo

    result["ok"] = not errors
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
