#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ockit -- OpenCore EFI 命令行工具箱（OCAuxiliaryTools 的 CLI 等价实现 + 扩展）

- 仅依赖 Python 3.8+ 标准库，Windows / macOS / Linux 开箱即用
- 功能：config 查看/编辑/校验、kext 上游检查与安装更新、ACPI 管理、
        补丁 verbatim 导入、SMBIOS 生成(macserial)、OpenCore 升级/键位同步、
        config diff、EFI 快照重建(snapshot)、备份、体检报告

用法示例：
    python ockit.py info  EFI/OC/config.plist
    python ockit.py validate EFI --download
    python ockit.py kext check EFI
    python ockit.py patch import EFI/OC/config.plist src.plist --match "Sequoia" --min 24.2.0
"""

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
import plistlib
from datetime import datetime
from pathlib import Path

VERSION = "0.1.0"

# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------


class ToolError(Exception):
    """可预期的用户级错误（打印信息后以退出码 2 结束）。"""


COLOR_ENABLED = [sys.stdout.isatty() and os.environ.get("NO_COLOR") is None]


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if COLOR_ENABLED[0] else str(s)


def ok(s):   print(_c("32", "[OK]  ") + str(s))


def warn(s): print(_c("33", "[WARN]") + " " + str(s))


def fail(s): print(_c("31", "[ERR] ") + " " + str(s))


def info_line(s): print(_c("36", "[INFO]") + " " + str(s))


def die(msg, code=2):
    fail(msg)
    sys.exit(code)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"


# ----------------------------------------------------------------------------
# plist 读写（保留原格式与键序）
# ----------------------------------------------------------------------------


def load_plist(path):
    with open(path, "rb") as f:
        data = f.read()
    cfg = plistlib.loads(data)
    fmt = plistlib.FMT_BINARY if data.startswith(b"bplist00") else plistlib.FMT_XML
    return cfg, fmt


def save_plist(cfg, path, fmt):
    with open(path, "wb") as f:
        plistlib.dump(cfg, f, fmt=fmt, sort_keys=False)


# ----------------------------------------------------------------------------
# GitHub 客户端
# ----------------------------------------------------------------------------

GH_API = "https://api.github.com"


def _gh_headers():
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ockit/{VERSION}",
    }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or settings_get("GitHubToken"))
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def gh_get(url, timeout=None, retries=None):
    s = get_settings()
    timeout = int(timeout or s.get("Timeout") or 30)
    retries = int(retries if retries is not None else s.get("Retries") or 3)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_gh_headers())
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < retries - 1:
                time.sleep(2 * (i + 1))  # 可能是限流
                last = e
                continue
            raise ToolError(f"GitHub API {e.code}: {e.reason} ({url})")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise ToolError(f"网络请求失败: {url} ({last})")


def gh_latest_release(repo, include_prerelease=False):
    if include_prerelease:
        rels = gh_get(api_url(f"/repos/{repo}/releases?per_page=10"))
        if not rels:
            raise ToolError(f"{repo}: 无任何 release")
        return rels[0]
    return gh_get(api_url(f"/repos/{repo}/releases/latest"))


def gh_download(url, dest, timeout=None, retries=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = get_settings()
    timeout = int(timeout or s.get("Timeout") or 120)
    retries = int(retries if retries is not None else s.get("Retries") or 3)
    last = None
    for i in range(retries):
        try:
            real_url = apply_mirror(url)
            if real_url != url:
                info_line(f"经镜像下载: {real_url}")
            req = urllib.request.Request(real_url, headers=_gh_headers())
            with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise ToolError(f"下载失败: {url} ({last})")


def safe_extract_zip(zip_path, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ToolError(f"zip 内发现不安全路径: {info.filename}")
        z.extractall(dest)
    return dest


def cache_dir():
    d = Path.home() / ".ockit" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------------------------
# ockit 自身设置（OckitSettings.plist，机制与 checklist 同构）
#
# 查找顺序：--settings 显式 > 环境变量 OCKIT_SETTINGS > 当前目录 >
#           脚本同目录 > ~/.ockit/OckitSettings.plist > 内置默认
# 可配置项（顶层键，全部可选）：
#   DefaultEFI    — kext/snapshot/oc upgrade 的默认 EFI 目录
#   DefaultConfig — validate/check 等的默认 config.plist 路径
#   Checklist     — KextChecklist.plist 的额外查找位置
#   BackupDir     — backup 默认输出目录
#   GitHubToken   — GitHub API token（环境变量 GITHUB_TOKEN/GH_TOKEN 优先）
#   GitHubApi     — JSON API 基址，默认 https://api.github.com，可指向兼容镜像
#   GitHubMirror  — 下载镜像模板：含 {url} 占位符（如 https://ghproxy.net/{url}）
#                   或纯前缀两种写法；留空直连
#   Timeout / Retries — 网络超时秒数 / 重试次数
#   Prerelease    — kext check/install 默认接受预发布版
#   Color         — 是否使用 ANSI 颜色
# ----------------------------------------------------------------------------

SETTINGS_FILENAME = "OckitSettings.plist"

DEFAULT_SETTINGS = {
    "DefaultEFI": "",
    "DefaultConfig": "",
    "Checklist": "",
    "BackupDir": "",
    "GitHubToken": "",
    "GitHubApi": "",
    "GitHubMirror": "",
    "Timeout": 30,
    "Retries": 3,
    "Prerelease": False,
    "Color": True,
}

_SETTINGS = {"loaded": False, "src": None, "data": dict(DEFAULT_SETTINGS)}

COLOR_ENABLED = [True]  # 容器化全局，settings 加载后可翻转


def settings_header_comment():
    return (
        "ockit 自身设置。所有键可选，缺省值见下；未知键会被忽略并告警。\n"
        "查找顺序：--settings > 环境变量 OCKIT_SETTINGS > 当前目录 >\n"
        "脚本同目录 > ~/.ockit/OckitSettings.plist > 内置默认。\n"
        "生成模板：ockit settings init"
    )


def _find_settings_file(explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("OCKIT_SETTINGS")
    if env:
        candidates.append(Path(env))
    try:
        candidates.append(Path.cwd() / SETTINGS_FILENAME)
    except OSError:
        pass
    candidates.append(Path(__file__).resolve().parent / SETTINGS_FILENAME)
    candidates.append(Path.home() / ".ockit" / SETTINGS_FILENAME)
    for c in candidates:
        if c and c.is_file():
            return c
    return None


def load_settings(explicit=None):
    """加载生效设置，返回 (data, source 描述)。幂等：explicit 变化时重载。"""
    force = explicit is not None
    if _SETTINGS["loaded"] and not force:
        return _SETTINGS["data"], _SETTINGS["src"]
    path = _find_settings_file(explicit)
    data = copy.deepcopy(DEFAULT_SETTINGS)
    if path:
        with open(path, "rb") as f:
            user = plistlib.load(f)
        if not isinstance(user, dict):
            raise ToolError(f"{path}: settings 根必须是 dict")
        for k, v in user.items():
            if str(k).startswith("#"):
                continue
            if k not in DEFAULT_SETTINGS:
                warn(f"settings 未知键 {k}，已忽略")
                continue
            data[k] = v
        src = str(path)
    else:
        src = "builtin-default（未找到外部 settings）"
    _SETTINGS["loaded"] = True
    _SETTINGS["src"] = src
    _SETTINGS["data"] = data
    COLOR_ENABLED[0] = bool(data.get("Color", True)) and os.environ.get("NO_COLOR") is None
    return data, src


def get_settings():
    if not _SETTINGS["loaded"]:
        load_settings(None)
    return _SETTINGS["data"]


def settings_get(key, default=None):
    v = get_settings().get(key, default)
    return v if v not in ("", None) else default


def api_url(path):
    """按 settings 的 GitHubApi 组装 JSON API 地址。"""
    base = (settings_get("GitHubApi") or GH_API).rstrip("/")
    return f"{base}{path}"


def apply_mirror(url, mirror=None):
    """按 settings 的 GitHubMirror 重写下载 URL。

    支持 {url} 占位符模板与纯前缀两种写法；未配置镜像时原样返回。
    """
    mirror = mirror if mirror is not None else settings_get("GitHubMirror")
    if not mirror:
        return url
    mirror = str(mirror).rstrip("/")
    if "{url}" in mirror:
        return mirror.replace("{url}", url)
    return f"{mirror}/{url}"


def resolve_efi(args_efi):
    """命令的 EFI 目录解析：CLI 参数 > settings.DefaultEFI。"""
    p = args_efi or settings_get("DefaultEFI")
    if not p:
        raise ToolError("未指定 EFI 目录（传参或用 settings 的 DefaultEFI）")
    return Path(p)


def resolve_config(args_config, efi_dir=None):
    """config 路径解析：CLI 参数 > settings.DefaultConfig > EFI/OC/config.plist。"""
    if args_config:
        return Path(args_config)
    p = settings_get("DefaultConfig")
    if p:
        return Path(p)
    if efi_dir:
        return Path(efi_dir) / "OC" / "config.plist"
    raise ToolError("未指定 config.plist（传参或用 settings 的 DefaultConfig）")


def download_oc_release(version=None, include_prerelease=False):
    """下载 OpenCorePkg release 到缓存目录，返回解压根目录。"""
    rel = gh_latest_release("acidanthera/OpenCorePkg", include_prerelease)
    if version and rel["tag_name"] != version.lstrip("v"):
        # 尝试指定版本
        rel = gh_get(api_url(f"/repos/acidanthera/OpenCorePkg/releases/tags/{version.lstrip('v')}"))
    asset = None
    for a in rel.get("assets", []):
        if a["name"].endswith("RELEASE.zip") and "DEBUG" not in a["name"]:
            asset = a
            break
    if asset is None:
        raise ToolError("OpenCore release 中未找到 RELEASE.zip")
    tag = rel["tag_name"]
    zpath = cache_dir() / f"OpenCore-{tag}-RELEASE.zip"
    extract_to = cache_dir() / f"OpenCore-{tag}"
    if not extract_to.exists() or not (extract_to / "X64").exists():
        if not zpath.exists():
            info_line(f"下载 OpenCore {tag} ...")
            gh_download(asset["browser_download_url"], zpath)
        info_line(f"解压 {zpath.name} (sha256={sha256_file(zpath)[:12]}...)")
        safe_extract_zip(zpath, extract_to)
    return extract_to, tag


def find_platform_binary(base, name):
    """在解压目录中寻找当前平台可执行文件。返回路径或 None。"""
    base = Path(base)
    if platform.system() == "Windows":
        cands = list(base.rglob(f"{name}.exe"))
    else:
        cands = [p for p in base.rglob(name) if not p.name.endswith(".exe")]
    for c in cands:
        if platform.system() != "Windows":
            c.chmod(c.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return c
    return None


def run_tool(binpath, args, timeout=60):
    if platform.system() == "Darwin":
        # Gatekeeper: 对下载的命令行工具去除隔离标记（若失败交由上层提示）
        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(binpath)],
                       capture_output=True)
    p = subprocess.run([str(binpath)] + list(args), capture_output=True,
                       text=True, timeout=timeout)
    return p


# ----------------------------------------------------------------------------
# kext checklist：上游取件清单（独立 plist 可由用户维护，解决 OCAT 清单不可改的弊端）
#
# 文件格式（XML plist）：
#   - 顶层键 = 工具内部名（kext install/check 用的名字）
#   - 每项：Repo("owner/repo") / Asset(资产名正则) / Provides(包内 .kext 列表)
#           Prerelease(bool) / MinKernel / MaxKernel / Alias(对外别名)
#   - 顶层 "Remove" 数组：按内部名删除内置条目
#   - 条目里 "Disabled"=true：停用该条目
#   - 以 "#" 开头的键视为注释
#
# 查找顺序：--checklist 显式 > 环境变量 OCKIT_CHECKLIST > 当前目录 >
#           脚本同目录 > ~/.ockit/KextChecklist.plist > 内置默认
# ----------------------------------------------------------------------------

DEFAULT_CHECKLIST = {
    "Lilu": {"repo": "acidanthera/Lilu", "asset": r"Lilu-1\.\d+\.\d+-RELEASE\.zip",
             "provides": ["Lilu.kext"]},
    "VirtualSMC": {"repo": "acidanthera/VirtualSMC", "asset": r"VirtualSMC-\d+\.\d+\.\d+-RELEASE\.zip",
                   "provides": ["VirtualSMC.kext", "SMCBatteryManager.kext",
                                "SMCLightSensor.kext", "SMCProcessor.kext", "SMCSuperIO.kext"]},
    "AppleALC": {"repo": "acidanthera/AppleALC", "asset": r"AppleALC-\d+\.\d+\.\d+-RELEASE\.zip",
                 "provides": ["AppleALC.kext"]},
    "NVMeFix": {"repo": "acidanthera/NVMeFix", "asset": r"NVMeFix-\d+\.\d+\.\d+-RELEASE\.zip",
                "provides": ["NVMeFix.kext"]},
    "RestrictEvents": {"repo": "acidanthera/RestrictEvents",
                       "asset": r"RestrictEvents-\d+\.\d+\.\d+-RELEASE\.zip",
                       "provides": ["RestrictEvents.kext"]},
    "BrightnessKeys": {"repo": "acidanthera/BrightnessKeys",
                       "asset": r"BrightnessKeys-\d+\.\d+\.\d+-RELEASE\.zip",
                       "provides": ["BrightnessKeys.kext"]},
    "FeatureUnlock": {"repo": "acidanthera/FeatureUnlock",
                      "asset": r"FeatureUnlock-\d+\.\d+\.\d+-RELEASE\.zip",
                      "provides": ["FeatureUnlock.kext"]},
    "VoodooI2C": {"repo": "acidanthera/VoodooI2C",
                  "asset": r"VoodooI2C-\d+\.\d+\.\d+-RELEASE\.zip",
                  "provides": ["VoodooI2C.kext", "VoodooI2CHID.kext"]},
    "AMFIPass": {"repo": "kaoskinkae/AMFIPass",
                 "asset": r"AMFIPass\.kext\.zip",
                 "provides": ["AMFIPass.kext"]},
    "ECEnabler": {"repo": "1Revenger1/ECEnabler", "asset": r"ECEnabler-\d+\.\d+\.\d+-RELEASE\.zip",
                  "provides": ["ECEnabler.kext"]},
    "IntelBluetoothFirmware": {"repo": "OpenIntelWireless/IntelBluetoothFirmware",
                               "asset": r"IntelBluetooth-v?\d+\.\d+\.\d+\.zip",
                               "provides": ["IntelBluetoothFirmware.kext", "IntelBTPatcher.kext",
                                            "BlueToolFixup.kext"]},
    "NootedRed": {"repo": "weachy/NootedRed", "asset": r"NootedRed-\d+\.\d+\.\d+-RELEASE\.zip",
                  "provides": ["NootedRed.kext"]},
    "ForgedInvariant": {"repo": "ChefKissInc/ForgedInvariant",
                        "asset": r"ForgedInvariant-v?\d+\.\d+\.\d+-RELEASE\.zip",
                        "provides": ["ForgedInvariant.kext"]},
    "SMCRadeonSensors": {"repo": "ChefKissInc/SMCRadeonSensors",
                         "asset": r"SMCRadeonSensors-v?\d+\.\d+\.\d+-RELEASE\.zip",
                         "provides": ["SMCRadeonSensors.kext"]},
    "AMDRyzenCPUPowerManagement": {"repo": "trulyspinach/SMCAMDProcessor",
                                   "asset": [r"AMDRyzenCPUPowerManagement\.kext\.zip",
                                             r"SMCAMDProcessor\.kext\.zip"],
                                   "provides": ["AMDRyzenCPUPowerManagement.kext",
                                                "SMCAMDProcessor.kext"]},
    "USBToolBox": {"repo": "corpnewt/USBToolBox", "asset": r"USBToolBox-\d+\.\d+\.\d+-RELEASE\.zip",
                   "provides": ["USBToolBox.kext"]},
    "AirportItlwm-Sequoia": {"repo": "laobamac/itlwm", "asset": r".*Sequoia.*RELEASE\.zip",
                             "prerelease": True,
                             "provides": ["AirportItlwm.kext"], "minkernel": "24.2.0",
                             "maxkernel": "24.99.99", "alias": "AirportItlwm"},
    "AirportItlwm-Tahoe": {"repo": "laobamac/itlwm", "asset": r".*Tahoe.*RELEASE\.zip",
                           "prerelease": True,
                           "provides": ["AirportItlwm.kext"], "minkernel": "25.0.0",
                           "alias": "AirportItlwm"},
    "SimpleHDA": {"repo": "laobamac/SimpleHDA", "asset": r"SimpleHDA-.*RELEASE\.zip",
                  "prerelease": True, "provides": ["SimpleHDA.kext"],
                  "minkernel": "25.0.0"},
}

# 兼容别名（只读场景）
KEXTDB = DEFAULT_CHECKLIST

CHECKLIST_FILENAME = "KextChecklist.plist"

_CHECKLIST_STATE = {"db": None, "src": None}


def load_checklist(explicit=None):
    """加载生效的 checklist，返回 (db, source 描述)。"""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("OCKIT_CHECKLIST")
    if env:
        candidates.append(Path(env))
    ck = settings_get("Checklist")  # settings 的 Checklist 位置
    if ck:
        candidates.append(Path(ck))
    try:
        candidates.append(Path.cwd() / CHECKLIST_FILENAME)
    except OSError:
        pass
    candidates.append(Path(__file__).resolve().parent / CHECKLIST_FILENAME)
    candidates.append(Path.home() / ".ockit" / CHECKLIST_FILENAME)
    for c in candidates:
        if c and c.is_file():
            with open(c, "rb") as f:
                user = plistlib.load(f)
            if not isinstance(user, dict):
                raise ToolError(f"{c}: checklist 根必须是 dict")
            return merge_checklist(DEFAULT_CHECKLIST, user), str(c)
    return copy.deepcopy(DEFAULT_CHECKLIST), "builtin-default（未找到外部 checklist）"


def merge_checklist(base, user):
    """用户条目覆盖同名内置项 / 新增 / Remove 删除 / Disabled 停用。"""
    db = {k: copy.deepcopy(v) for k, v in base.items() if not str(k).startswith("#")}
    for name in user.get("Remove", []) or []:
        db.pop(str(name), None)
    for k, v in user.items():
        if str(k).startswith("#") or k == "Remove":
            continue
        if not isinstance(v, dict):
            warn(f"checklist 条目 {k} 不是 dict，已忽略")
            continue
        if v.get("Disabled"):
            db.pop(str(k), None)
            continue
        entry = copy.deepcopy(db.get(str(k), {}))
        for uk, uv in v.items():
            entry[str(uk).lower()] = uv
        db[str(k)] = entry
    return db


def set_checklist(explicit=None):
    _CHECKLIST_STATE["db"], _CHECKLIST_STATE["src"] = load_checklist(explicit)
    return _CHECKLIST_STATE["src"]


def get_checklist():
    if _CHECKLIST_STATE["db"] is None:
        set_checklist(None)
    return _CHECKLIST_STATE["db"], _CHECKLIST_STATE["src"]

# kext 依赖排序
LILU = "Lilu.kext"
LILU_PLUGINS = {
    "NootedRed.kext", "AppleALC.kext", "RestrictEvents.kext", "FeatureUnlock.kext",
    "BlueToolFixup.kext", "NVMeFix.kext", "SMCRadeonSensors.kext",
}
PLUGIN_PARENTS = {
    "VoodooInput.kext": "VoodooI2C.kext",
    "VoodooI2CServices.kext": "VoodooI2C.kext",
    "VoodooGPIO.kext": "VoodooI2C.kext",
    "VoodooI2CHID.kext": "VoodooI2C.kext",
    "UTBMap.kext": "USBToolBox.kext",
    "SMCBatteryManager.kext": "VirtualSMC.kext",
    "SMCLightSensor.kext": "VirtualSMC.kext",
    "SMCProcessor.kext": "VirtualSMC.kext",
    "SMCSuperIO.kext": "VirtualSMC.kext",
}


def _bundle_base(bundle):
    return str(bundle).split("/")[0]


def strip_kext(name):
    name = str(name)
    return name[:-5] if name.endswith(".kext") else name


def _order_base(bundle):
    b = str(bundle)
    if "/Contents/PlugIns/" in b:
        return b.split("/")[-1]
    return b.split("/")[0]


def order_score(bundle):
    base = _order_base(bundle)
    if base == LILU:
        return 0
    if base == "VirtualSMC.kext":
        return 2
    parent = PLUGIN_PARENTS.get(base)
    if parent == "VirtualSMC.kext":
        return 2.5
    if base in LILU_PLUGINS:
        return 3
    if base == "USBToolBox.kext":
        return 4
    if base == "UTBMap.kext":
        return 5
    if base == "VoodooI2C.kext":
        return 6
    if parent == "VoodooI2C.kext":
        return 7
    return 10


def sort_kext_entries(entries):
    indexed = sorted(enumerate(entries),
                     key=lambda t: (order_score(t[1].get("BundlePath", "")), t[0]))
    return [e for _, e in indexed]


# ----------------------------------------------------------------------------
# config 点路径访问
# ----------------------------------------------------------------------------


def cfg_get(cfg, dotted, default=None):
    cur = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def cfg_set(cfg, dotted, value):
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def cfg_rm(cfg, dotted):
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            raise ToolError(f"路径不存在: {dotted}")
        cur = cur[p]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        raise ToolError(f"路径不存在: {dotted}")
    return cur.pop(parts[-1])


def fmt_value(v):
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# boot-args -------------------------------------------------------------------

BOOTARGS_WARN = {
    "debug=0x100": "常驻 debug 输出会拖慢启动，日常使用建议移除",
    "-bfixup": "htmambo fork 专属参数，官方/weachy NootedRed 不识别",
    "-vi2c-force-polling": "轮询模式更兼容但 CPU 占用高，中断模式可用时建议移除",
    "-lilubetaall": "对 Lilu 插件全量解除版本限制，正式版系统上不需要",
}


def bootargs_get(cfg):
    return cfg_get(cfg, "NVRAM.Add.7C436110-AB2A-4BBB-A880-FE41995C9F82.boot-args", "") or ""


def bootargs_set(cfg, value):
    cfg_set(cfg, "NVRAM.Add.7C436110-AB2A-4BBB-A880-FE41995C9F82.boot-args", value.strip())


def bootargs_modify(cfg, add=(), remove=()):
    args = bootargs_get(cfg).split()
    for r in remove:
        args = [a for a in args if a != r and not a.startswith(r + "=")]
    for a in add:
        key = a.split("=")[0]
        args = [x for x in args if x != a and not x.startswith(key + "=")]
        args.append(a)
    bootargs_set(cfg, " ".join(args))


# ----------------------------------------------------------------------------
# kext / ACPI 目录与 config 同步操作
# ----------------------------------------------------------------------------


def read_kext_version(kext_path):
    ip = Path(kext_path) / "Contents" / "Info.plist"
    if not ip.exists():
        return None
    try:
        with open(ip, "rb") as f:
            d = plistlib.load(f)
        return d.get("CFBundleShortVersionString") or d.get("CFBundleVersion")
    except Exception:  # noqa: BLE001
        return None


def find_kext_dirs(root):
    """扫描目录下所有 .kext（跳过 PlugIns 内层）。"""
    root = Path(root)
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.kext")):
        out.append(p)
    return out


def make_kext_entry(kext_path, minkernel="", maxkernel="", enabled=True):
    kext_path = Path(kext_path)
    ip = kext_path / "Contents" / "Info.plist"
    if not ip.exists():
        raise ToolError(f"{kext_path.name}: 缺少 Contents/Info.plist")
    with open(ip, "rb") as f:
        d = plistlib.load(f)
    entry = {
        "Arch": "x86_64",
        "BundlePath": kext_path.name,
        "Comment": "",
        "Enabled": enabled,
        "ExecutablePath": "",
        "MaxKernel": maxkernel,
        "MinKernel": minkernel,
        "PlistPath": "Contents/Info.plist",
    }
    exe = d.get("CFBundleExecutable")
    if exe and (kext_path / "Contents" / "MacOS" / exe).exists():
        entry["ExecutablePath"] = f"Contents/MacOS/{exe}"
    return entry


def ensure_kext_in_config(cfg, entry):
    add = cfg.setdefault("Kernel", {}).setdefault("Add", [])
    for i, e in enumerate(add):
        if e.get("BundlePath") == entry["BundlePath"]:
            add[i] = entry
            break
    else:
        add.append(entry)
    cfg["Kernel"]["Add"] = sort_kext_entries(add)
    return entry


def remove_kext_from_config(cfg, bundle_base):
    add = cfg.get("Kernel", {}).get("Add", [])
    keep = [e for e in add if _bundle_base(e.get("BundlePath", "")) != bundle_base]
    removed = len(add) - len(keep)
    if "Kernel" in cfg:
        cfg["Kernel"]["Add"] = keep
    return removed


def kext_db_lookup(bundle_base):
    name = bundle_base[:-len(".kext")] if bundle_base.endswith(".kext") else bundle_base
    if name in ("AirportItlwm",):
        # laobamac fork 提供两个分段版本
        return None
    db, _ = get_checklist()
    for db_name, meta in db.items():
        if bundle_base in meta.get("provides", []) or name == db_name:
            return db_name, meta
    return None


def install_kexts_from_zip(zip_path, kexts_dir, wanted=None):
    """从 release zip 解出 .kext 并拷入 Kexts 目录，返回安装的 kext 名列表。"""
    with tempfile.TemporaryDirectory() as td:
        root = safe_extract_zip(zip_path, td)
        installed = []
        for src in Path(root).rglob("*.kext"):
            rel = src.relative_to(root)
            if len(rel.parts) > 4:
                continue  # 跳过嵌套过深（PlugIns 内层由主 kext 自带）
            if wanted and src.name not in wanted:
                continue
            dst = Path(kexts_dir) / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            installed.append(src.name)
        if not installed:
            raise ToolError(f"{Path(zip_path).name} 中未找到可安装的 .kext")
        return installed


# ----------------------------------------------------------------------------
# 子命令实现
# ----------------------------------------------------------------------------


def cmd_info(args):
    cfg, _ = load_plist(args.config)
    print(f"config: {args.config}")
    print(f"  SMBIOS           : {cfg_get(cfg, 'PlatformInfo.Generic.SystemProductName', '?')}")
    print(f"  SecureBootModel  : {cfg_get(cfg, 'Misc.Security.SecureBootModel', '?')}")
    csr = cfg_get(cfg, "NVRAM.Add.7C436110-AB2A-4BBB-A880-FE41995C9F82.csr-active-config")
    print(f"  csr-active-config: {csr.hex() if isinstance(csr, bytes) else csr}")
    print(f"  boot-args        : {bootargs_get(cfg)}")
    kexts = cfg_get(cfg, "Kernel.Add", [])
    print(f"  Kernel.Add       : {len(kexts)} 条")
    for e in kexts:
        ver = read_kext_version(Path(args.config).parent / "Kexts" / _bundle_base(e["BundlePath"]))
        flag = "" if e.get("Enabled", True) else " [disabled]"
        mk = f" ({e.get('MinKernel','')}-{e.get('MaxKernel','')})" if e.get("MinKernel") or e.get("MaxKernel") else ""
        print(f"    - {e['BundlePath']:<55s} {ver or '?':<10s}{mk}{flag}")
    acpi = cfg_get(cfg, "ACPI.Add", [])
    print(f"  ACPI.Add         : {len(acpi)} 条 -> " + ", ".join(a.get("Path", "?") for a in acpi))
    patches = cfg_get(cfg, "Kernel.Patch", [])
    en = sum(1 for p in patches if p.get("Enabled"))
    print(f"  Kernel.Patch     : {len(patches)} 条（启用 {en}）")
    return 0


def cmd_config(args):
    cfg, fmt = load_plist(args.config)
    if args.action == "get":
        v = cfg_get(cfg, args.path, args.default if args.default is not None else None)
        print(fmt_value(v) if v is not None else "(未设置)")
    elif args.action == "set":
        if args.value is None:
            raise ToolError("set 需要一个 value")
        v = args.value
        if args.type == "int":
            v = int(v)
        elif args.type == "bool":
            v = v.lower() in ("1", "true", "yes", "on")
        elif args.type == "data":
            v = bytes.fromhex(v)
        cfg_set(cfg, args.path, v)
        save_plist(cfg, args.config, fmt)
        ok(f"{args.path} = {fmt_value(v)}")
    elif args.action == "rm":
        old = cfg_rm(cfg, args.path)
        save_plist(cfg, args.config, fmt)
        ok(f"已删除 {args.path} (原值: {fmt_value(old)[:80]})")
    return 0


def cmd_bootargs(args):
    cfg, fmt = load_plist(args.config)
    cur = bootargs_get(cfg)
    if args.action == "get":
        print(cur or "(空)")
    elif args.action == "set":
        bootargs_set(cfg, " ".join(args.args))
        save_plist(cfg, args.config, fmt)
    elif args.action == "add":
        bootargs_modify(cfg, add=args.args)
        save_plist(cfg, args.config, fmt)
    elif args.action == "remove":
        bootargs_modify(cfg, remove=args.args)
        save_plist(cfg, args.config, fmt)
    new = bootargs_get(cfg)
    if args.action != "get":
        ok(f"boot-args: {cur or '(空)'}  ->  {new or '(空)'}")
    for a in new.split():
        if a in BOOTARGS_WARN:
            warn(f"{a}: {BOOTARGS_WARN[a]}")
    return 0


# ---- kext -------------------------------------------------------------------


def collect_installed(kexts_dir):
    out = []
    for p in find_kext_dirs(kexts_dir):
        out.append({"name": p.name, "path": p, "version": read_kext_version(p)})
    return out


def pick_release_assets(repo, meta, prerelease=False, version=None):
    """按 checklist 的 Asset（字符串或正则列表）挑选 release 资产。

    返回 (release, [asset, ...])；Asset 为列表时逐个正则各匹配一个资产，
    用于上游把多个 kext 分装成多个 zip 的情况（如 trulyspinach/SMCAMDProcessor）。
    """
    rel = (gh_latest_release(repo, prerelease or meta.get("prerelease", False))
           if version is None
           else gh_get(api_url(f"/repos/{repo}/releases/tags/{version.lstrip('v')}")))
    pats = meta.get("asset")
    if isinstance(pats, str):
        pats = [pats]
    chosen = []
    for pat in pats:
        rx = re.compile(pat, re.I)
        cands = []
        for a in rel.get("assets", []):
            n = a["name"]
            if "DEBUG" in n.upper() or "dSYM" in n:
                continue
            if rx.search(n):
                score = 0 if "RELEASE" in n.upper() else 1
                cands.append((score, a))
        if not cands:
            raise ToolError(f"{repo} release {rel.get('tag_name')} 中没有匹配 {pat} 的资产")
        cands.sort(key=lambda t: t[0])
        chosen.append(cands[0][1])
    return rel, chosen


# 向后兼容的旧名
def pick_release_asset(repo, meta, prerelease=False, version=None):
    rel, assets = pick_release_assets(repo, meta, prerelease, version)
    return rel, assets[0]


def cmd_kext(args):
    src = set_checklist(getattr(args, "checklist", None))
    if args.action in ("check", "install", "update"):
        info_line(f"checklist 来源: {src}")
    efi = resolve_efi(args.efi)
    s = get_settings()
    args.prerelease = bool(getattr(args, "prerelease", False)) or bool(s.get("Prerelease"))
    kexts_dir = efi / "OC" / "Kexts"
    cfg_path = resolve_config(args.config, efi)
    cfg, fmt = load_plist(cfg_path)

    if args.action == "list":
        cfg_add = {e.get("BundlePath", "").split("/")[0]: e for e in cfg_get(cfg, "Kernel.Add", [])}
        for k in collect_installed(kexts_dir):
            entry = cfg_add.get(k["name"])
            status = "enabled " if (entry is None or entry.get("Enabled", True)) else "disabled"
            in_cfg = "" if entry else " (未在 config 中)"
            mk = ""
            if entry and (entry.get("MinKernel") or entry.get("MaxKernel")):
                mk = f" ({entry.get('MinKernel','')}-{entry.get('MaxKernel','')})"
            print(f"{k['name']:<50s} {k['version'] or '?':<10s} {status}{mk}{in_cfg}")
        return 0

    if args.action == "check":
        results = []
        for k in collect_installed(kexts_dir):
            hit = kext_db_lookup(k["name"])
            if hit is None:
                results.append((k["name"], k["version"], "local/custom", "-"))
                continue
            db_name, meta = hit
            try:
                rel, _ = pick_release_asset(meta["repo"], meta, prerelease=args.prerelease)
                upstream = rel["tag_name"].lstrip("v")
                results.append((k["name"], k["version"], upstream, meta["repo"]))
            except ToolError as e:
                results.append((k["name"], k["version"], f"ERR: {e}", meta["repo"]))
        w = max(len(r[0]) for r in results) if results else 20
        print(f"{'kext':<{w}}  {'已装':<10} {'上游最新':<14} 仓库")
        for name, v, up, repo in results:
            mark = ""
            if up.startswith("ERR"):
                mark = _c("31", " !")
            elif up not in ("-", "local/custom") and v and up.lstrip("v") != str(v).lstrip("v"):
                mark = _c("33", " ↑")
            else:
                mark = _c("32", " =")
            print(f"{name:<{w}}  {str(v):<10} {up:<14} {repo}{mark}")
        return 0

    if args.action == "install":
        db, src = get_checklist()
        db_name = args.kext if args.kext in db else None
        if db_name is None:
            hit = kext_db_lookup(args.kext if args.kext.endswith(".kext") else args.kext + ".kext")
            if hit is None:
                # Alias 兜底（如 AirportItlwm -> 两个分段条目）
                aliases = [k for k, m in db.items() if m.get("alias") == args.kext]
                if len(aliases) == 1:
                    db_name = aliases[0]
                elif len(aliases) > 1:
                    raise ToolError(f"{args.kext} 有多个分段条目，请用其中之一: "
                                    f"{' '.join(sorted(aliases))}")
                else:
                    raise ToolError(f"未知 kext: {args.kext}（可用: {' '.join(sorted(db))}；"
                                    f"checklist 来源: {src}）")
            else:
                db_name = hit[0]
        meta = db[db_name]
        rel, assets = pick_release_assets(meta["repo"], meta, prerelease=args.prerelease,
                                          version=args.version)
        installed = []
        for asset in assets:
            zpath = cache_dir() / asset["name"]
            if not zpath.exists():
                info_line(f"下载 {asset['name']} ...")
                gh_download(asset["browser_download_url"], zpath)
            installed += install_kexts_from_zip(zpath, kexts_dir,
                                                wanted=set(meta.get("provides", [])) or None)
        for name in installed:
            entry = make_kext_entry(Path(kexts_dir) / name,
                                    minkernel=meta.get("minkernel", ""),
                                    maxkernel=meta.get("maxkernel", ""))
            ensure_kext_in_config(cfg, entry)
            ok(f"安装并写入 config: {name} ({rel['tag_name']})")
        save_plist(cfg, cfg_path, fmt)
        return 0

    if args.action == "update":
        updated = 0
        for k in collect_installed(kexts_dir):
            if args.kext and strip_kext(args.kext) != strip_kext(k["name"]):
                continue
            hit = kext_db_lookup(k["name"])
            if hit is None:
                warn(f"{k['name']}: local/custom，跳过")
                continue
            db_name, meta = hit
            if meta.get("alias"):  # 分段件（AirportItlwm）不适合盲目 update
                warn(f"{k['name']}: 多分段 kext，请用 install 明确目标")
                continue
            try:
                rel, assets = pick_release_assets(meta["repo"], meta, prerelease=args.prerelease)
            except ToolError as e:
                warn(f"{k['name']}: {e}")
                continue
            upstream = rel["tag_name"].lstrip("v")
            if k["version"] and upstream == str(k["version"]).lstrip("v"):
                ok(f"{k['name']} 已是最新 ({upstream})")
                continue
            installed = []
            for asset in assets:
                zpath = cache_dir() / asset["name"]
                if not zpath.exists():
                    info_line(f"下载 {asset['name']} ...")
                    gh_download(asset["browser_download_url"], zpath)
                installed += install_kexts_from_zip(zpath, kexts_dir,
                                                    wanted=set(meta.get("provides", [])) or None)
            for name in installed:
                old = next((e for e in cfg_get(cfg, "Kernel.Add", [])
                            if _bundle_base(e["BundlePath"]) == name), None)
                entry = make_kext_entry(
                    Path(kexts_dir) / name,
                    minkernel=(old or {}).get("MinKernel", meta.get("minkernel", "")),
                    maxkernel=(old or {}).get("MaxKernel", meta.get("maxkernel", "")))
                entry["Enabled"] = (old or {}).get("Enabled", True)
                ensure_kext_in_config(cfg, entry)
                ok(f"更新 {name}: {k['version']} -> {upstream}")
                updated += 1
        if updated:
            save_plist(cfg, cfg_path, fmt)
        return 0

    if args.action in ("enable", "disable"):
        flag = args.action == "enable"
        hit = 0
        for e in cfg_get(cfg, "Kernel.Add", []):
            base = _bundle_base(e.get("BundlePath", ""))
            if strip_kext(args.kext) == strip_kext(base) or args.kext in e.get("BundlePath", ""):
                e["Enabled"] = flag
                hit += 1
        if hit:
            save_plist(cfg, cfg_path, fmt)
            ok(f"{args.kext}: {hit} 条目已{'启用' if flag else '禁用'}")
        else:
            raise ToolError(f"config 中找不到 {args.kext}")
        return 0

    if args.action == "remove":
        n = remove_kext_from_config(cfg, args.kext if args.kext.endswith(".kext") else args.kext + ".kext")
        if not n:
            raise ToolError(f"config 中找不到 {args.kext}")
        save_plist(cfg, cfg_path, fmt)
        target = kexts_dir / (args.kext if args.kext.endswith(".kext") else args.kext + ".kext")
        if args.delete and target.exists():
            shutil.rmtree(target)
            ok(f"config 移除 {n} 条，并删除 {target.name}")
        else:
            ok(f"config 移除 {n} 条（文件保留，加 --delete 连文件一起删）")
        return 0

    return 1


# ---- ACPI -------------------------------------------------------------------


def cmd_acpi(args):
    cfg, fmt = load_plist(args.config)
    acpi_dir = Path(args.config).parent / "ACPI"
    if args.action == "list":
        for a in cfg_get(cfg, "ACPI.Add", []):
            exists = (acpi_dir / a.get("Path", "")).exists()
            print(f"{'[x]' if a.get('Enabled') else '[ ]'} {a.get('Path','?')}"
                  f"{'' if exists else '  (文件缺失!)'}")
        return 0
    if args.action in ("enable", "disable"):
        flag = args.action == "enable"
        hit = 0
        for a in cfg_get(cfg, "ACPI.Add", []):
            if args.path.lower() in str(a.get("Path", "")).lower():
                a["Enabled"] = flag
                hit += 1
        if hit:
            save_plist(cfg, args.config, fmt)
            ok(f"{args.path}: {hit} 条目已{'启用' if flag else '禁用'}")
        else:
            raise ToolError(f"找不到 {args.path}")
        return 0
    if args.action == "add":
        p = Path(args.file)
        if not p.exists():
            raise ToolError(f"文件不存在: {p}")
        cfg.setdefault("ACPI", {}).setdefault("Add", []).append(
            {"Comment": "", "Enabled": True, "Path": p.name})
        save_plist(cfg, args.config, fmt)
        ok(f"已加入 {p.name}（记得把文件放到 ACPI 目录）")
        return 0
    if args.action == "remove":
        lst = cfg.get("ACPI", {}).get("Add", [])
        keep = [a for a in lst if args.path.lower() not in str(a.get("Path", "")).lower()]
        cfg["ACPI"]["Add"] = keep
        save_plist(cfg, args.config, fmt)
        ok(f"移除 {len(lst) - len(keep)} 条")
        return 0
    return 1


# ---- patch ------------------------------------------------------------------


def import_patches(cfg, src_patches, match=None, exact=None, min_=None, max_=None,
                   enable=None, force=False):
    """把来源补丁 verbatim 深拷贝进 cfg 的 Kernel.Patch，返回 (imported, skipped)。"""
    imported, skipped = 0, 0
    existing = {p.get("Comment", "") for p in cfg_get(cfg, "Kernel.Patch", [])}
    for p in src_patches:
        comment = str(p.get("Comment", ""))
        if match and match.lower() not in comment.lower():
            continue
        if exact and exact != comment:
            continue
        if comment in existing and not force:
            skipped += 1
            continue
        q = copy.deepcopy(p)
        if min_ is not None:
            q["MinKernel"] = min_
        if max_ is not None:
            q["MaxKernel"] = max_
        if enable is not None:
            q["Enabled"] = enable
        cfg.setdefault("Kernel", {}).setdefault("Patch", []).append(q)
        existing.add(comment)
        imported += 1
    return imported, skipped


def cmd_patch(args):
    cfg, fmt = load_plist(args.config)
    if args.action == "list":
        for p in cfg_get(cfg, "Kernel.Patch", []):
            mk = f" ({p.get('MinKernel','')}-{p.get('MaxKernel','')})" \
                if p.get("MinKernel") or p.get("MaxKernel") else ""
            print(f"{'[x]' if p.get('Enabled') else '[ ]'} {p.get('Comment','(无注释)')}{mk}")
        return 0
    if args.action in ("enable", "disable"):
        flag = args.action == "enable"
        hit = 0
        for p in cfg_get(cfg, "Kernel.Patch", []):
            if args.match.lower() in str(p.get("Comment", "")).lower():
                p["Enabled"] = flag
                hit += 1
        if hit:
            save_plist(cfg, args.config, fmt)
            ok(f"{hit} 条补丁已{'启用' if flag else '禁用'}")
        else:
            raise ToolError(f"没有匹配 '{args.match}' 的补丁")
        return 0
    if args.action == "import":
        src, _ = load_plist(args.source)
        patches = cfg_get(src, "Kernel.Patch", [])
        if not patches:
            raise ToolError(f"{args.source} 中没有 Kernel.Patch")
        imported, skipped = import_patches(
            cfg, patches, match=args.match, exact=args.exact,
            min_=args.min, max_=args.max, enable=args.enable, force=args.force)
        if imported:
            save_plist(cfg, args.config, fmt)
        ok(f"导入 {imported} 条补丁" + (f"，跳过重复 {skipped} 条" if skipped else ""))
        return 0
    return 1


# ---- SMBIOS -----------------------------------------------------------------

KNOWN_SMBIOS = [
    "MacBookPro16,2", "MacBookPro16,3", "MacBookPro16,1", "iMac20,1", "iMac20,2",
    "iMacPro1,1", "MacPro7,1",
]


def cmd_smbios(args):
    cfg_path = args.config
    cfg, fmt = (load_plist(cfg_path) if cfg_path and Path(cfg_path).exists() else (None, None))
    if args.action == "show":
        if cfg is None:
            raise ToolError("请用 --in 指定 config.plist")
        g = cfg_get(cfg, "PlatformInfo.Generic", {})
        for k in ("SystemProductName", "SystemSerialNumber", "MLB", "SystemUUID", "ROM"):
            v = g.get(k, "")
            if k == "ROM" and isinstance(v, bytes):
                v = v.hex()
            print(f"{k:20s} {v}")
        return 0
    if args.action == "gen":
        if args.model:
            model = args.model
        elif cfg is not None:
            model = cfg_get(cfg, "PlatformInfo.Generic.SystemProductName", "")
        else:
            raise ToolError("请指定 --model 或 --in")
        if model not in KNOWN_SMBIOS:
            warn(f"{model} 不在常见列表中，继续执行")
        oc_root, tag = download_oc_release()
        binname = "macserial"
        binpath = find_platform_binary(oc_root, binname)
        if binpath is None:
            raise ToolError(f"OpenCore {tag} 中未找到 macserial（Linux 需自行编译或换平台）")
        p = run_tool(binpath, ["--model", model, "--generate", "-n", "1"])
        if p.returncode != 0 or not p.stdout.strip():
            raise ToolError(f"macserial 失败: {p.stderr.strip()}")
        line = p.stdout.strip().splitlines()[0]
        serial, _, mlb = line.partition("|")
        serial, mlb = serial.strip(), mlb.strip()
        print(f"Model : {model}")
        print(f"Serial: {serial}")
        print(f"MLB   : {mlb}")
        if cfg is not None and not args.print_only:
            g = cfg.setdefault("PlatformInfo", {}).setdefault("Generic", {})
            g["SystemProductName"] = model
            g["SystemSerialNumber"] = serial
            g["MLB"] = mlb
            import uuid as _uuid
            g["SystemUUID"] = str(_uuid.uuid4()).upper()
            save_plist(cfg, cfg_path, fmt)
            ok(f"已写入 config 并生成新 SystemUUID（ROM 保留，请自行确认）")
        return 0
    return 1


# ---- OpenCore 升级 / diff / resync -------------------------------------------


def cmd_oc(args):
    oc_root, tag = download_oc_release(version=args.version)
    if args.action == "download":
        ok(f"OpenCore {tag} 已缓存于 {oc_root}")
        return 0
    efi = resolve_efi(args.efi)
    config = resolve_config(args.config, efi)
    if args.action == "upgrade":
        sample = oc_root / "X64" / "EFI" / "OC" / "config.plist"
        replaced = []
        mapping = [
            (oc_root / "X64" / "EFI" / "BOOT" / "BOOTx64.efi", efi / "BOOT" / "BOOTx64.efi"),
            (oc_root / "X64" / "EFI" / "OC" / "OpenCore.efi", efi / "OC" / "OpenCore.efi"),
        ]
        for src, dst in mapping:
            if src.exists() and dst.parent.exists():
                shutil.copy2(src, dst)
                replaced.append(dst.name)
        drivers_src = oc_root / "X64" / "EFI" / "OC" / "Drivers"
        drivers_dst = efi / "OC" / "Drivers"
        if drivers_dst.exists() and drivers_src.exists():
            for f in drivers_src.glob("*.efi"):
                if f.name in ("OpenRuntime.efi",) or (drivers_dst / f.name).exists():
                    shutil.copy2(f, drivers_dst / f.name)
                    replaced.append(f"Drivers/{f.name}")
        ok(f"已替换二进制: {', '.join(replaced)}")
        info_line(f"config 与 Sample({tag}) 的键位差异如下（不自动修改）：")
        sample_cfg, _ = load_plist(sample)
        user_cfg, _ = load_plist(config)
        report_diff(sample_cfg, user_cfg, base="", only_schema=True)
        return 0
    if args.action == "resync":
        sample = oc_root / "X64" / "EFI" / "OC" / "config.plist"
        sample_cfg, _ = load_plist(sample)
        user_cfg, user_fmt = load_plist(args.config)
        added = resync_cfg(sample_cfg, user_cfg)
        save_plist(user_cfg, args.config, user_fmt)
        ok(f"补入缺失键 {added} 个（已有键未覆盖）")
        return 0
    return 1


SKIP_DIFF_KEYS = {"#WARNING - 1", "#WARNING - 2", "#WARNING - 3", "#WARNING - 4", "#WARNING - 5", "#WARNING - 6"}


def iter_keys(d):
    return [k for k in d.keys() if k not in SKIP_DIFF_KEYS and not str(k).startswith("#")]


def report_diff(a, b, base="", only_schema=False, out=print):
    """报告 a 有 b 无（缺失）、b 有 a 无（多余/自定义）、值不同。"""
    for k in iter_keys(a):
        if k not in b:
            out(f"  [缺失] {base}{k}  (Sample 默认: {fmt_value(a[k])[:60]})")
        elif isinstance(a[k], dict) and isinstance(b.get(k), dict):
            report_diff(a[k], b[k], f"{base}{k}.", only_schema, out)
        elif not only_schema and a[k] != b[k]:
            out(f"  [不同] {base}{k}: {fmt_value(a[k])[:40]} -> {fmt_value(b[k])[:40]}")
    for k in iter_keys(b):
        if k not in a:
            out(f"  [多余] {base}{k}")


def resync_cfg(sample, user, base_path=""):
    added = 0
    for k in iter_keys(sample):
        if k not in user:
            user[k] = copy.deepcopy(sample[k])
            added += 1
        elif isinstance(sample[k], dict) and isinstance(user.get(k), dict):
            added += resync_cfg(sample[k], user[k], f"{base_path}{k}.")
    return added


def cmd_diff(args):
    a, fa = load_plist(args.a)
    b, fb = load_plist(args.b)
    report_diff(a, b, only_schema=args.schema)
    return 0


# ---- validate ----------------------------------------------------------------

KERNEL_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def validate_config(cfg, efi_dir, out=print):
    problems, warnings = [], []
    efi_dir = Path(efi_dir)
    oc_dir = efi_dir / "OC"

    # ACPI.Add
    for a in cfg_get(cfg, "ACPI.Add", []):
        p = oc_dir / "ACPI" / a.get("Path", "")
        if not p.exists():
            problems.append(f"ACPI 文件缺失: {a.get('Path')}")
    # ACPI enable 但文件缺失已覆盖；重复 path
    paths = [a.get("Path") for a in cfg_get(cfg, "ACPI.Add", [])]
    for p in set(paths):
        if paths.count(p) > 1:
            problems.append(f"ACPI 重复条目: {p}")

    # Kernel.Add
    add = cfg_get(cfg, "Kernel.Add", [])
    seen = {}
    for e in add:
        key = (str(e.get("BundlePath", "")), str(e.get("MinKernel", "")),
               str(e.get("MaxKernel", "")))
        seen[key] = seen.get(key, 0) + 1
    for (bp, mn, mx), n in seen.items():
        if n > 1 and not (bp == "AirportItlwm.kext"):
            problems.append(f"kext 重复条目: {bp} ({mn}-{mx})")
    for e in add:
        bp = str(e.get("BundlePath", ""))
        kpath = oc_dir / "Kexts" / bp
        if not kpath.exists():
            problems.append(f"kext 文件缺失: {bp}")
        else:
            if e.get("PlistPath") and not (kpath / e["PlistPath"]).exists():
                problems.append(f"kext PlistPath 无效: {bp}")
            exe = e.get("ExecutablePath")
            if exe and not (kpath / exe).exists():
                problems.append(f"kext ExecutablePath 无效: {bp}/{exe}")
        for key in ("MinKernel", "MaxKernel"):
            v = str(e.get(key, "") or "")
            if v and not KERNEL_RE.match(v):
                problems.append(f"{bp}: {key} 格式非法: {v}")
        if e.get("MinKernel") and e.get("MaxKernel") and e["MinKernel"] > e["MaxKernel"]:
            problems.append(f"{bp}: MinKernel > MaxKernel")
    # 顺序检查：只关心依赖关系（Lilu 首位、插件在父 kext 之后），
    # 独立 kext 与 Lilu 插件之间的相对顺序无所谓，不做泛化告警
    if add and _bundle_base(add[0].get("BundlePath", "")) != LILU:
        has_lilu = any(_bundle_base(e.get("BundlePath", "")) == LILU for e in add)
        if has_lilu:
            warnings.append("Lilu.kext 不在 Kernel.Add 首位")
    for e in add:
        bp = str(e.get("BundlePath", ""))
        if "/Contents/PlugIns/" in bp:
            continue  # 插件条目自身经 BundlePath 全路径引用，父已在同一 kext 内
        base = _order_base(bp)
        parent = PLUGIN_PARENTS.get(base)
        if parent and parent != "VirtualSMC.kext":
            idx_p = next((i for i, x in enumerate(add)
                          if _bundle_base(str(x.get("BundlePath", ""))) == base), None)
            idx_par = next((i for i, x in enumerate(add)
                            if _bundle_base(str(x.get("BundlePath", ""))) == parent), None)
            if idx_p is not None and idx_par is not None and idx_p < idx_par:
                warnings.append(f"插件在父 kext 之前: {base} 应在 {parent} 之后")

    # UEFI Drivers
    for d in cfg_get(cfg, "UEFI.Drivers", []):
        p = oc_dir / "Drivers" / d.get("Path", "")
        if not p.exists():
            problems.append(f"UEFI Driver 缺失: {d.get('Path')}")

    # SMBIOS
    model = cfg_get(cfg, "PlatformInfo.Generic.SystemProductName", "")
    if model and model not in KNOWN_SMBIOS:
        warnings.append(f"SMBIOS {model} 不在常见推荐列表")
    if model == "MacBookPro16,3":
        warnings.append("MacBookPro16,3 已被 macOS 26 放弃支持；如升级 Tahoe 请换 MacBookPro16,2")

    # csr
    csr = cfg_get(cfg, "NVRAM.Add.7C436110-AB2A-4BBB-A880-FE41995C9F82.csr-active-config")
    if isinstance(csr, bytes) and len(csr) != 4:
        problems.append("csr-active-config 应为 4 字节")

    for a in bootargs_get(cfg).split():
        if a in BOOTARGS_WARN:
            warnings.append(f"boot-arg {a}: {BOOTARGS_WARN[a]}")

    for p in problems:
        out(f"{_c('31','[ERR]')} {p}")
    for w in warnings:
        out(f"{_c('33','[WARN]')} {w}")
    if not problems and not warnings:
        out(_c("32", "config 基础校验全部通过"))
    return problems, warnings


def cmd_validate(args):
    config = resolve_config(args.config)
    cfg, _ = load_plist(config)
    efi_dir = config.parent.parent
    problems, warnings = validate_config(cfg, efi_dir)
    if args.ocvalidate or args.download:
        binpath = Path(args.ocvalidate) if args.ocvalidate else None
        if binpath is None:
            oc_root, tag = download_oc_release()
            binpath = find_platform_binary(oc_root, "ocvalidate")
            if binpath is None:
                raise ToolError(f"OpenCore {tag} 中未找到 ocvalidate")
        p = run_tool(binpath, [str(args.config)])
        info_line(f"ocvalidate 输出（退出码 {p.returncode}）:")
        print(p.stdout.strip() or "(无输出)")
        if p.returncode != 0:
            return 3
    return 1 if problems else 0


# ---- snapshot ----------------------------------------------------------------


def cmd_snapshot(args):
    set_checklist(getattr(args, "checklist", None))
    efi = resolve_efi(args.efi)
    oc_dir = efi / "OC"
    cfg_path = resolve_config(args.config, efi)
    cfg, fmt = load_plist(cfg_path)

    old_acpi = {a.get("Path"): a for a in cfg_get(cfg, "ACPI.Add", [])}
    old_kext = {e.get("BundlePath"): e for e in cfg_get(cfg, "Kernel.Add", [])}
    old_drv = {d.get("Path"): d for d in cfg_get(cfg, "UEFI.Drivers", [])}

    new_acpi = []
    for f in sorted((oc_dir / "ACPI").glob("*.aml")) if (oc_dir / "ACPI").exists() else []:
        old = old_acpi.get(f.name, {})
        new_acpi.append({"Comment": old.get("Comment", ""), "Enabled": old.get("Enabled", True),
                         "Path": f.name})

    new_kext = []
    for p in find_kext_dirs(oc_dir / "Kexts"):
        old = old_kext.get(p.name, {})
        mk = old.get("MinKernel", "")
        mxk = old.get("MaxKernel", "")
        if not mk:
            db, _ = get_checklist()
            for meta in db.values():
                if p.name in meta.get("provides", []):
                    mk = meta.get("minkernel", mk)
                    mxk = meta.get("maxkernel", mxk)
                    break
        new_kext.append(make_kext_entry(p, minkernel=mk, maxkernel=mxk,
                                        enabled=old.get("Enabled", True)))
        plugins = p / "Contents" / "PlugIns"
        if plugins.exists():
            for pl in sorted(plugins.glob("*.kext")):
                old_pl = old_kext.get(f"{p.name}/Contents/PlugIns/{pl.name}", {})
                rel = make_kext_entry(pl, minkernel=old_pl.get("MinKernel", mk),
                                      maxkernel=old_pl.get("MaxKernel", mxk),
                                      enabled=old_pl.get("Enabled", True))
                rel["BundlePath"] = f"{p.name}/Contents/PlugIns/{pl.name}"
                new_kext.append(rel)
    new_kext = sort_kext_entries(new_kext)

    new_drv = []
    for f in sorted((oc_dir / "Drivers").glob("*.efi")) if (oc_dir / "Drivers").exists() else []:
        old = old_drv.get(f.name, {})
        new_drv.append({"Arguments": old.get("Arguments", ""), "Comment": old.get("Comment", ""),
                        "Enabled": old.get("Enabled", True), "Path": f.name})

    counts = (len(new_acpi), len(new_kext), len(new_drv))
    if args.dry_run:
        info_line(f"[dry-run] 将重建 ACPI {counts[0]} / kext {counts[1]} / drivers {counts[2]} 条")
        for e in new_kext:
            print(f"    {e['BundlePath']}  ({e.get('MinKernel','')}-{e.get('MaxKernel','')})"
                  f"{'' if e.get('Enabled', True) else ' [disabled]'}")
        return 0
    cfg["ACPI"]["Add"] = new_acpi
    cfg.setdefault("Kernel", {})["Add"] = new_kext
    cfg.setdefault("UEFI", {})["Drivers"] = new_drv
    save_plist(cfg, cfg_path, fmt)
    ok(f"snapshot 完成: ACPI {counts[0]} / kext {counts[1]} / drivers {counts[2]}")
    return 0


# ---- backup / check ----------------------------------------------------------


def cmd_backup(args):
    efi = resolve_efi(args.efi)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"EFI-backup-{stamp}.zip"
    if args.out:
        out = Path(args.out)
    else:
        bdir = settings_get("BackupDir")
        out = (Path(bdir) if bdir else efi.parent) / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(efi.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(efi.parent))
    ok(f"备份完成: {out} ({human_size(out.stat().st_size)})")
    return 0


def cmd_check(args):
    config = resolve_config(args.config)
    cfg, _ = load_plist(config)
    efi_dir = config.parent.parent
    problems, warnings = validate_config(cfg, efi_dir, out=lambda s: print(s))
    lines = [f"# ockit 体检报告 — {datetime.now():%Y-%m-%d %H:%M}",
             f"- config: `{config}`",
             f"- 校验: {len(problems)} 错误 / {len(warnings)} 警告"]
    if args.net:
        info_line("检查 kext 上游更新 ...")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        ns = argparse.Namespace(efi=str(efi_dir), config=args.config, action="check",
                                prerelease=False, kext=None, version=None, delete=False,
                                checklist=None)
        with redirect_stdout(buf):
            try:
                cmd_kext(ns)
            except ToolError as e:
                warn(f"kext check 失败: {e}")
        print(buf.getvalue())
        lines.append("## kext 上游对比")
        lines.append("```")
        lines.append(buf.getvalue().rstrip())
        lines.append("```")
    if args.markdown:
        md = "\n".join(lines)
        Path(args.markdown).write_text(md, encoding="utf-8")
        ok(f"报告已写入 {args.markdown}")
    return 1 if problems else 0


# ---- checklist ---------------------------------------------------------------


def checklist_header_comment():
    return (
        "ockit kext checklist — 用户可维护的上游取件清单（解决 OCAT 清单不可改的弊端）\n"
        "顶层键 = 工具内部名；字段：Repo(owner/repo)、Asset(资产名正则)、\n"
        "Provides(包内 .kext 列表)、Prerelease(bool)、MinKernel/MaxKernel、Alias。\n"
        "覆盖同名内置条目；新增键即新增条目；顶层 Remove 数组可删除内置条目；\n"
        "条目内 Disabled=true 表示停用。查找顺序：--checklist > 环境变量 OCKIT_CHECKLIST\n"
        "> 当前目录 > 脚本同目录 > ~/.ockit/KextChecklist.plist > 内置默认。"
    )


def cmd_checklist(args):
    if args.action == "show":
        src = set_checklist(args.checklist)
        db, _ = get_checklist()
        print(f"checklist 来源: {src}")
        print(f"{'内部名':<28} {'仓库':<36} {'Provides'}")
        for name, meta in sorted(db.items()):
            print(f"{name:<28} {meta.get('repo','?'):<36} {', '.join(meta.get('provides', []))}")
        return 0

    if args.action == "init":
        target = Path(args.path) if args.path else Path.cwd() / CHECKLIST_FILENAME
        if target.exists() and not args.force:
            raise ToolError(f"{target} 已存在（用 --force 覆盖）")
        out = {"#comment": checklist_header_comment()}
        for name, meta in DEFAULT_CHECKLIST.items():
            item = {}
            for k, v in meta.items():
                if k == "alias":
                    item["Alias"] = v
                elif k == "minkernel":
                    item["MinKernel"] = v
                elif k == "maxkernel":
                    item["MaxKernel"] = v
                elif k == "prerelease":
                    item["Prerelease"] = v
                else:
                    item[k.capitalize()] = v
            out[name] = item
        with open(target, "wb") as f:
            plistlib.dump(out, f, fmt=plistlib.FMT_XML, sort_keys=False)
        ok(f"已生成可编辑 checklist: {target}")
        info_line("编辑后放置于 当前目录 / 脚本同目录 / ~/.ockit/ 均可生效")
        return 0

    if args.action == "validate":
        path = Path(args.path) if args.path else None
        db, src = load_checklist(path)
        problems = []
        for name, meta in db.items():
            if not meta.get("repo"):
                problems.append(f"{name}: 缺少 Repo")
            if not meta.get("asset"):
                problems.append(f"{name}: 缺少 Asset")
            else:
                pats = meta["asset"] if isinstance(meta["asset"], list) else [meta["asset"]]
                for pat in pats:
                    try:
                        re.compile(pat)
                    except re.error as e:
                        problems.append(f"{name}: Asset 正则非法 ({e})")
            if not meta.get("provides"):
                problems.append(f"{name}: 缺少 Provides")
        for p in problems:
            fail(p)
        if problems:
            raise ToolError(f"checklist 校验失败（来源: {src}）")
        ok(f"checklist 校验通过: {len(db)} 个条目（来源: {src}）")
        return 0
    return 1


# ---- settings ----------------------------------------------------------------


def cmd_settings(args):
    if args.action == "show":
        data, src = load_settings(args.path)
        print(f"settings 来源: {src}")
        for k in DEFAULT_SETTINGS:
            v = data.get(k)
            if k == "GitHubToken" and v:
                v = str(v)[:4] + "****"
            print(f"{k:<14} {v}")
        return 0

    if args.action == "init":
        target = Path(args.path) if args.path else Path.cwd() / SETTINGS_FILENAME
        if target.exists() and not args.force:
            raise ToolError(f"{target} 已存在（用 --force 覆盖）")
        out = {"#comment": settings_header_comment()}
        out.update(copy.deepcopy(DEFAULT_SETTINGS))
        save_plist(out, target, plistlib.FMT_XML)
        ok(f"已生成可编辑 settings: {target}")
        info_line("编辑后放置于 当前目录 / 脚本同目录 / ~/.ockit/ 均可生效")
        return 0

    if args.action == "validate":
        data, src = load_settings(args.path)
        problems = []
        type_map = {"Timeout": int, "Retries": int, "Prerelease": bool, "Color": bool}
        for k, v in data.items():
            if k in type_map and not isinstance(v, type_map[k]):
                problems.append(f"{k}: 应为 {type_map[k].__name__}，实际 {type(v).__name__}")
        for p in problems:
            fail(p)
        if problems:
            raise ToolError(f"settings 校验失败（来源: {src}）")
        ok(f"settings 校验通过（来源: {src}）")
        return 0
    return 1


# ----------------------------------------------------------------------------
# 命令行入口
# ----------------------------------------------------------------------------


def build_parser():
    ap = argparse.ArgumentParser(prog="ockit",
                                 description="OpenCore EFI 命令行工具箱（OCAT 的 CLI 等价 + 扩展）")
    ap.add_argument("--version", action="version", version=f"ockit {VERSION}")
    ap.add_argument("--settings", help="指定 OckitSettings.plist 路径（全局，置于子命令前）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="打印 config 概要")
    p.add_argument("config")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("config", help="点路径 get/set/rm")
    p.add_argument("action", choices=["get", "set", "rm"])
    p.add_argument("config")
    p.add_argument("path")
    p.add_argument("value", nargs="?")
    p.add_argument("--type", choices=["str", "int", "bool", "data"], default="str")
    p.add_argument("--default", help="get 时的缺省显示")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("bootargs", help="boot-args get/set/add/remove")
    p.add_argument("action", choices=["get", "set", "add", "remove"])
    p.add_argument("config")
    p.add_argument("args", nargs="*")
    p.set_defaults(func=cmd_bootargs)

    p = sub.add_parser("kext", help="kext list/check/install/update/enable/disable/remove")
    p.add_argument("action", choices=["list", "check", "install", "update",
                                      "enable", "disable", "remove"])
    p.add_argument("efi", nargs="?", help="EFI 目录（可用 settings 的 DefaultEFI）")
    p.add_argument("kext", nargs="?", help="kext 名（可省略）")
    p.add_argument("--config", help="config 路径（默认 EFI/OC/config.plist）")
    p.add_argument("--version", dest="version", help="指定上游版本")
    p.add_argument("--prerelease", action="store_true", help="接受预发布版")
    p.add_argument("--delete", action="store_true", help="remove 时连文件删除")
    p.add_argument("--checklist", help="指定 KextChecklist.plist 路径")
    p.set_defaults(func=cmd_kext)

    p = sub.add_parser("acpi", help="ACPI list/enable/disable/add/remove")
    p.add_argument("action", choices=["list", "enable", "disable", "add", "remove"])
    p.add_argument("config")
    p.add_argument("path", nargs="?")
    p.add_argument("--file", help="add 时的本地文件")
    p.set_defaults(func=cmd_acpi)

    p = sub.add_parser("patch", help="内核补丁 list/import/enable/disable")
    p.add_argument("action", choices=["list", "import", "enable", "disable"])
    p.add_argument("config")
    p.add_argument("source", nargs="?", help="import 的来源 config")
    p.add_argument("--match", help="按 Comment 子串过滤")
    p.add_argument("--exact", help="按 Comment 精确匹配")
    p.add_argument("--min", dest="min", help="覆盖 MinKernel")
    p.add_argument("--max", dest="max", help="覆盖 MaxKernel")
    p.add_argument("--enable", dest="enable", action="store_true", help="导入后启用")
    p.add_argument("--disable", dest="enable", action="store_false", help="导入后禁用")
    p.add_argument("--force", action="store_true", help="Comment 重复也强制导入")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("smbios", help="SMBIOS show/gen")
    p.add_argument("action", choices=["show", "gen"])
    p.add_argument("config", nargs="?")
    p.add_argument("--model")
    p.add_argument("--print-only", action="store_true", help="只打印不写入")
    p.set_defaults(func=cmd_smbios)

    p = sub.add_parser("oc", help="OpenCore download/upgrade/resync")
    p.add_argument("action", choices=["download", "upgrade", "resync"])
    p.add_argument("efi", nargs="?", help="EFI 目录（upgrade/resync 必需，可用 settings）")
    p.add_argument("--config", help="config 路径")
    p.add_argument("--version", dest="version", help="指定 OC 版本")
    p.set_defaults(func=cmd_oc)

    p = sub.add_parser("diff", help="两个 config 的递归 diff")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--schema", action="store_true", help="只比较键位，不比较值")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("validate", help="内置校验 + 可选 ocvalidate")
    p.add_argument("config", nargs="?", help="config 路径（可用 settings 的 DefaultConfig）")
    p.add_argument("--ocvalidate", help="指定 ocvalidate 路径")
    p.add_argument("--download", action="store_true", help="自动下载 OpenCore 并用官方 ocvalidate")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("snapshot", help="按 EFI 目录重建 ACPI/kext/driver 条目")
    p.add_argument("efi", nargs="?", help="EFI 目录（可用 settings 的 DefaultEFI）")
    p.add_argument("--config")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--checklist", help="指定 KextChecklist.plist 路径")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("checklist", help="kext 上游清单 show/init/validate")
    p.add_argument("action", choices=["show", "init", "validate"])
    p.add_argument("path", nargs="?", help="checklist 文件路径")
    p.add_argument("--checklist", help="show 时的外部 checklist")
    p.add_argument("--force", action="store_true", help="init 覆盖已有文件")
    p.set_defaults(func=cmd_checklist)

    p = sub.add_parser("backup", help="打包 EFI 目录")
    p.add_argument("efi", nargs="?", help="EFI 目录（可用 settings 的 DefaultEFI）")
    p.add_argument("--out")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("check", help="体检报告（校验 + 可选上游检查）")
    p.add_argument("config", nargs="?", help="config 路径（可用 settings 的 DefaultConfig）")
    p.add_argument("--net", action="store_true", help="联网检查 kext 更新")
    p.add_argument("--markdown", help="输出 markdown 报告路径")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("settings", help="ockit 自身设置 show/init/validate")
    p.add_argument("action", choices=["show", "init", "validate"])
    p.add_argument("path", nargs="?", help="settings 文件路径")
    p.add_argument("--force", action="store_true", help="init 覆盖已有文件")
    p.set_defaults(func=cmd_settings)

    return ap


def main(argv=None):
    ap = build_parser()
    ns = ap.parse_args(argv)
    try:
        load_settings(getattr(ns, "settings", None))
        return ns.func(ns) or 0
    except ToolError as e:
        fail(str(e))
        return 2
    except KeyboardInterrupt:
        warn("已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
