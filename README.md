# ockit — OpenCore EFI 命令行工具箱

[OCAuxiliaryTools](https://github.com/ic005k/OCAuxiliaryTools) 的 CLI 等价实现 + 同类工具扩展。
单文件、仅 Python 3.8+ 标准库，**Windows / macOS / Linux 开箱即用**，无需 pip 安装。

## 安装

ockit 只分发 **Python 脚本**，不打包二进制；Python 环境由使用者自行准备。
ockit 仅依赖 Python 3.8+ 标准库，不需要 pip 安装任何东西。

```bash
python ockit.py --help      # 任何 Python 3.8+，无需 pip
# 或用包装脚本（自动寻找 py/python3/python，找不到时给出安装指引）
ockit.bat   # Windows
./ockit.sh  # macOS / Linux
```

各平台 Python 前提：

| 平台 | 说明 |
|---|---|
| Linux | `python3` 几乎都预装 |
| macOS | 首次运行弹"安装命令行开发者工具"，确认后自动装好 python3 |
| Windows | 需装一次：`winget install Python.Python.3.12`（wrapper 会自动提示） |

macOS 首次运行下载的辅助二进制（macserial/ocvalidate）可能被 Gatekeeper 拦截：

```bash
xattr -d com.apple.quarantine ~/.ockit/cache/OpenCore-*/Utilities/**/macserial
```

## 功能总览

| 命令 | 说明 | 对应 OCAT / 同类工具功能 |
|---|---|---|
| `info <config>` | 打印 config 概要（SMBIOS/boot-args/kext 版本表/补丁统计） | OCAT 配置概览 |
| `validate <config>` | 内置校验（文件缺失/重复/顺序/MinKernel 格式/已知坑参数）+ 可选官方 `ocvalidate` | OCAT 校验 + ProperTree Snapshot 检查 |
| `config get/set/rm` | 点路径读写任意键 | OCAT 配置编辑器 |
| `bootargs get/set/add/remove` | boot-args 操作 + 已知参数风险提示 | OCAT boot-args 编辑 |
| `kext list/check/install/update/remove` | kext 清单、**上游 Release 版本对比**、下载安装/更新（自动写回 config 并修正顺序） | OCAT Kext 更新器 |
| `acpi list/enable/disable/add/remove` | SSDT 条目管理 | OCAT ACPI 管理器 |
| `patch list/import/enable/disable` | **内核补丁 verbatim 导入**（deepcopy，不经过手抄），支持按 Comment 过滤和 MinKernel/MaxKernel 覆盖 | 扩展（OCConfigCompare 工作流） |
| `smbios show/gen` | 生成 SMBIOS（调用 OC 官方 `macserial`），直接写回 config | OCAT SMBIOS 生成器 |
| `oc download/upgrade/resync` | OpenCore 升级：替换二进制 + 与 Sample.plist 的键位 diff + `resync` 补入缺失键 | OCAT OC 更新 + OCConfigCompare |
| `diff a.plist b.plist` | 两个 config 的递归 diff（`--schema` 只比键位） | OCConfigCompare |
| `snapshot <efi>` | 按 EFI 目录**重建** ACPI/Add、Kernel/Add（含 PlugIns 子条目）、UEFI/Drivers，自动推断顺序与内核版本段 | ProperTree "OC Clean Snapshot" |
| `backup <efi>` | 打包 EFI 为带时间戳 zip | OCAT 备份 |
| `check <config>` | 体检报告：校验结果 + 可选联网 kext 更新检查，可输出 markdown | 扩展 |

## ockit 自身设置：OckitSettings.plist

与 checklist 同机制的用户配置文件，免掉每次重复敲路径/开关：

```bash
ockit settings init       # 在当前目录生成模板
ockit settings show       # 生效值 + 来源
ockit settings validate   # 类型校验
```

| 键 | 作用 | 默认 |
|---|---|---|
| `DefaultEFI` | kext/snapshot/backup/oc upgrade 的默认 EFI 目录 | — |
| `DefaultConfig` | validate/check 等的默认 config.plist | — |
| `Checklist` | KextChecklist.plist 的额外查找位置 | — |
| `BackupDir` | backup 默认输出目录 | EFI 同级 |
| `GitHubToken` | GitHub API token（环境变量优先）。**强烈建议配置**：匿名 API 每小时仅 60 次，`kext check` 一次就会用掉几十次 | — |
| `GitHubApi` | JSON API 基址，可指向兼容 GitHub API 的镜像/加速服务 | `https://api.github.com` |
| `GitHubMirror` | **下载镜像模板**（对标 OCAT 的镜像配置）：`{url}` 占位符或纯前缀两种写法，留空直连 | — |
| `Timeout` / `Retries` | 网络超时秒数 / 重试次数 | 30 / 3 |
| `Prerelease` | 默认接受预发布 kext | false |
| `Color` | ANSI 颜色 | true |

查找顺序与 checklist 一致：`--settings`（全局，置于子命令前）> 环境变量
`OCKIT_SETTINGS` > 当前目录 > 脚本同目录 > `~/.ockit/OckitSettings.plist` > 内置默认。
示例——配好 `DefaultEFI` 后，`ockit validate` / `ockit kext list` 无需任何参数。

### GitHub 镜像（国内加速，对标 OCAT）

`GitHubMirror` 只重写**下载 URL**（release 资产、OpenCore zip），API JSON 走 `GitHubApi`：

```xml
<!-- 前缀风格（ghproxy 系镜像通用） -->
<key>GitHubMirror</key>
<string>https://ghproxy.net/</string>

<!-- 或 {url} 占位符风格 -->
<key>GitHubMirror</key>
<string>https://mirror.example.com/?u={url}</string>

<!-- API 也可换基址（需镜像方兼容 GitHub REST API） -->
<key>GitHubApi</key>
<string>https://ghapi.example.com</string>
```

使用镜像时每次下载会打印实际 URL（降级可见）；注意各镜像站可用性多变，请自行选择
当前可用的服务。另外规避 API 限流优先用 `GitHubToken`（镜像治下载，token 治限流）。

## kext checklist：可自行维护的上游清单（对比 OCAT 的核心改进）

OCAT 的 kext 更新清单编译在程序里，用户无法修改。ockit 把它做成独立数据文件
**`KextChecklist.plist`**（XML plist，任意编辑器可改）：

```bash
ockit checklist init          # 在当前目录生成可编辑模板
ockit checklist show          # 查看当前生效清单及来源
ockit checklist validate      # 校验条目完整性（Repo/Asset 正则/Provides）
ockit kext check EFI --checklist 我的清单.plist   # 显式指定
```

条目字段：`Repo`（owner/repo）、`Asset`（资产名正则，**可为数组**——适配上游把多个
kext 分装成多个 zip 的情况）、`Provides`（包内 .kext 列表）、`Prerelease`、
`MinKernel`/`MaxKernel`、`Alias`。

合并语义（用户文件 vs 内置默认）：

| 操作 | 写法 |
|---|---|
| 覆盖内置条目（如换镜像源） | 写同名键，只列要改的字段，其余继承 |
| 新增条目 | 写一个新键 |
| 删除内置条目 | 顶层 `Remove` 数组 |
| 停用条目 | 该条目加 `Disabled = true` |
| 注释 | 任何以 `#` 开头的键 |

文件查找顺序：`--checklist` 显式指定 > 环境变量 `OCKIT_CHECKLIST` > 当前目录 >
脚本同目录 > `~/.ockit/KextChecklist.plist` > 内置默认（`kext check` 会显示实际来源，
降级可见）。

### 内置清单（19 项）

Lilu / VirtualSMC 系 / AppleALC / NVMeFix / RestrictEvents / FeatureUnlock /
BrightnessKeys / VoodooI2C / AMFIPass(kaoskinkae 镜像) / ECEnabler /
IntelBluetoothFirmware(含 BlueToolFixup) / NootedRed(weachy) / ForgedInvariant /
SMCRadeonSensors / AMDRyzenCPUPowerManagement / USBToolBox /
AirportItlwm(laobamac fork, Sequoia/Tahoe 分段) / SimpleHDA(laobamac)。

不在清单中的 kext（如本地自生成的 USBMap.kext）会被标记为 `local/custom` 并跳过上游检查。
注意：对"多 kext 合包"的条目（如 IntelBluetoothFirmware 包内的 BlueToolFixup），
版本对比的是包的 release tag 而非该 kext 自身版本号。

支持环境变量 `GITHUB_TOKEN` / `GH_TOKEN` 提升速率限制。

## 典型工作流：Hackintosh EFI 升级（示例）

```bash
# 0. 备份
ockit backup /Volumes/EFI/EFI

# 1. 体检（含联网检查 kext 更新）
ockit check EFI/OC/config.plist --net --markdown report.md

# 2. 升级 OpenCore 并查看键位差异，resync 补齐新键
ockit oc upgrade /Volumes/EFI/EFI
ockit oc resync /Volumes/EFI/EFI/OC/config.plist

# 3. 从参考 EFI verbatim 导入 15.0+/26.x 内核补丁变体
ockit patch import EFI/OC/config.plist ref-config.plist --match "26.4" --force
ockit patch import EFI/OC/config.plist ref-config.plist --match "15.0" --force

# 4. 批量更新 kext（自动下载 + 写回 config + 依赖排序）
ockit kext update EFI

# 5. 校验
ockit validate EFI/OC/config.plist --download
```

## 设计原则

1. **verbatim 优先**：补丁/配置迁移全部用 `copy.deepcopy` 原样搬运，杜绝手抄 Find/Replace 出错。
2. **机器校验兜底**：所有写操作之后都应跑 `validate`（内置规则 + 官方 ocvalidate）。
3. **顺序即语义**：Lilu 首位、VirtualSMC 次之、插件紧随父 kext；snapshot 与 install 自动修正。
4. **降级可见**：上游不可达/本地自定义件一律明示，不静默跳过。

## 测试

```bash
python -m unittest discover -s tests
```

覆盖：plist 读写（格式/键序保持）、点路径访问、boot-args 操作、补丁导入（bytes 保真/去重/
版本段覆盖）、kext 依赖排序、snapshot 重建、校验器、上游数据库映射。

## License

MIT
