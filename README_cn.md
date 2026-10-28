# ockit — OpenCore EFI 命令行工具箱

[OCAuxiliaryTools（OCAT）](https://github.com/ic005k/OCAuxiliaryTools) 的 CLI 等价实现 + 同类工具扩展。单文件 Python、**仅标准库**（Python 3.8+，无需 pip），**Windows / macOS / Linux** 开箱即用。

[English](README.md) | 简体中文

## 为什么做 ockit

| 图形化工具的痛点 | ockit 的做法 |
|---|---|
| kext 更新清单编译在程序里，用户改不了 | 上游清单独立为**可编辑的 `KextChecklist.plist`**——覆盖、新增、删除、停用随意 |
| 手抄 Find/Replace 迁移补丁（易错） | 内核补丁 **verbatim 导入**（`deepcopy` 原样搬运）——字节级保真 |
| 跳过/回退了什么看不到 | 一切降级**明示**（`local/custom`、上游不可达等），绝不静默 |
| 国内访问 GitHub 困难 | 内置 **`GitHubMirror` / `GitHubApi`** 镜像设置（对标 OCAT 的镜像配置） |

## 环境要求

Python 3.8+，**零第三方依赖**——不需要 pip、不需要 venv。包装脚本自动寻找 `py`/`python3`/`python`，找不到时打印对应平台的一条安装指引。

| 平台 | 说明 |
|---|---|
| Linux | `python3` 几乎都预装 |
| macOS | 首次运行可能弹"安装命令行开发者工具"，确认后自动装好 python3 |
| Windows | 需装一次：`winget install Python.Python.3.12` |

辅助二进制（`macserial`、`ocvalidate`）按需从 OpenCore 官方 Release 下载。macOS 可能被 Gatekeeper 拦截：

```bash
xattr -d com.apple.quarantine ~/.ockit/cache/OpenCore-*/Utilities/**/macserial
```

## 命令总览

| 命令 | 说明 |
|---|---|
| `info <config>` | config 概要：SMBIOS / boot-args / kext 版本表 / 补丁统计 |
| `validate <config>` | 内置校验（文件缺失/重复/加载顺序/MinKernel 格式/已知坑参数）+ 可选官方 `ocvalidate`（`--download` 自动获取） |
| `config get/set/rm` | 点路径读写任意键 |
| `bootargs get/set/add/remove` | boot-args 操作 + 已知参数风险提示 |
| `acpi list/enable/disable/add/remove` | SSDT 条目管理 |
| `kext list/check/install/update/remove` | kext 清单、**上游 Release 版本对比**、下载安装/更新（自动写回 config 并修正顺序）；本地自定义件标记 `local/custom` |
| `patch list/import/enable/disable` | **内核补丁 verbatim 导入**，支持按 Comment 过滤与 MinKernel/MaxKernel 覆盖 |
| `smbios show/gen` | 调 OC 官方 `macserial` 生成 SMBIOS，直接写回 config |
| `oc download/upgrade/resync` | OpenCore 升级：替换二进制 + 与 Sample.plist 键位 diff + `resync` 补入新键 |
| `diff a.plist b.plist` | 递归 config diff（`--schema` 只比键位） |
| `snapshot <efi>` | 按 EFI 目录**重建** ACPI/Add、Kernel/Add（含 PlugIns 子条目）、UEFI/Drivers——ProperTree "OC Clean Snapshot" 等价 |
| `backup <efi>` | EFI 打包为带时间戳 zip |
| `check <config>` | 体检报告：校验 + 可选联网 kext 检查，可输出 markdown |
| `checklist show/init/validate` | 管理用户可维护的上游清单 |
| `settings show/init/validate` | 管理 ockit 自身设置 |

## kext checklist：`KextChecklist.plist`（对比 OCAT 的核心改进）

```bash
ockit checklist init          # 在当前目录生成可编辑模板
ockit checklist show          # 当前生效清单及来源
ockit kext check EFI --checklist 我的清单.plist
```

条目字段：`Repo`（owner/repo）、`Asset`（资产名正则，**可为数组**——适配上游多 zip 分装）、`Provides`（包内 .kext 列表）、`Prerelease`、`MinKernel`/`MaxKernel`、`Alias`。

合并语义（用户文件 vs 内置默认）：

| 操作 | 写法 |
|---|---|
| 覆盖内置条目（如换镜像源） | 写同名键，只列要改的字段，其余继承 |
| 新增条目 | 写一个新键 |
| 删除内置条目 | 顶层 `Remove` 数组 |
| 停用条目 | 该条目加 `Disabled = true` |
| 注释 | 任何以 `#` 开头的键 |

查找顺序：`--checklist` 显式指定 > 环境变量 `OCKIT_CHECKLIST` > 当前目录 > 脚本同目录 > `~/.ockit/` > 内置默认。`kext check` 始终打印实际来源，降级可见。内置清单约 19 项（Lilu、VirtualSMC 系、AppleALC、AMFIPass、IntelBluetoothFirmware、NootedRed、USBToolBox、laobamac 的 AirportItlwm/SimpleHDA 等）。已知限制：多 kext 合包条目（如 IntelBluetoothFirmware 包内的 BlueToolFixup）版本对比的是包的 release tag。

环境变量 `GITHUB_TOKEN` / `GH_TOKEN`（或 `GitHubToken` 设置）可提升 API 限流——**建议配置**，匿名额度每小时仅 60 次。

## ockit 自身设置：`OckitSettings.plist`

```bash
ockit settings init && ockit settings show
```

| 键 | 作用 | 默认 |
|---|---|---|
| `DefaultEFI` | kext/snapshot/backup/oc 的默认 EFI 目录 | — |
| `DefaultConfig` | validate/check 的默认 config.plist | — |
| `Checklist` | KextChecklist.plist 的额外查找位置 | — |
| `BackupDir` | backup 输出目录 | EFI 同级 |
| `GitHubToken` | GitHub API token（环境变量优先） | — |
| `GitHubApi` | JSON API 基址（可指向兼容 REST API 的镜像服务） | `https://api.github.com` |
| `GitHubMirror` | **下载 URL 重写**（release 资产、OpenCore zip）；`{url}` 占位符或纯前缀 | — |
| `Timeout` / `Retries` | 网络超时秒数 / 重试次数 | 30 / 3 |
| `Prerelease` | 默认接受预发布 kext | false |
| `Color` | ANSI 颜色 | true |

查找顺序与 checklist 一致（全局参数 `--settings` > 环境变量 `OCKIT_SETTINGS` > 当前目录 > 脚本同目录 > `~/.ockit/` > 内置默认）。配好 `DefaultEFI` 后，`ockit validate` / `ockit kext list` 无需任何参数。

### GitHub 镜像（国内加速，对标 OCAT）

```xml
<!-- 前缀风格（ghproxy 系镜像通用） -->
<key>GitHubMirror</key>
<string>https://ghproxy.net/</string>

<!-- 或 {url} 占位符风格 -->
<key>GitHubMirror</key>
<string>https://mirror.example.com/?u={url}</string>
```

走镜像的每次下载都会打印实际 URL（降级可见）。镜像治**下载**，API 限流优先用 `GitHubToken` 解决。

## 典型工作流：Hackintosh EFI 升级

```bash
ockit backup /Volumes/EFI/EFI
ockit check EFI/OC/config.plist --net --markdown report.md
ockit oc upgrade /Volumes/EFI/EFI
ockit oc resync /Volumes/EFI/EFI/OC/config.plist
ockit patch import EFI/OC/config.plist ref-config.plist --match "26.4" --force
ockit kext update EFI
ockit validate EFI/OC/config.plist --download
```

## 设计原则

1. **verbatim 优先**——补丁/配置一律 `deepcopy` 原样搬运；手抄 Find/Replace 是 config 变砖的头号原因。
2. **机器校验兜底**——任何写操作之后跑 `validate`（内置规则 + 官方 ocvalidate）。
3. **顺序即语义**——Lilu 首位、VirtualSMC 次之、插件紧随父 kext；snapshot/install 自动修正。
4. **降级可见**——没有任何静默跳过。

## 测试

```bash
python -m unittest discover -s tests
```

31 项离线单测覆盖：plist 读写（格式/键序保持）、点路径访问、boot-args 操作、补丁导入（bytes 保真/去重/版本段覆盖）、kext 依赖排序、snapshot 重建、校验器、settings/checklist 合并语义。

## 许可

MIT，见 [LICENSE](LICENSE)。与 Acidanthera 团队及 OCAT 无关联；kext 与 OpenCore 均从各自官方上游 Release 下载。

## 致谢

- [OCAuxiliaryTools](https://github.com/ic005k/OCAuxiliaryTools)——ockit 对标工作流的图形化工具
- [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) / Acidanthera——OpenCore、`macserial`、`ocvalidate`、Lilu 及全家桶
- [ProperTree](https://github.com/corpnewt/ProperTree)——ockit 的 snapshot 语义来源
- 内置清单引用到的全部上游 kext 作者
