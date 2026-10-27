# ockit — OpenCore EFI 命令行工具箱

[OCAuxiliaryTools](https://github.com/ic005k/OCAuxiliaryTools) 的 CLI 等价实现 + 同类工具扩展。
单文件、仅 Python 3.8+ 标准库，**Windows / macOS / Linux 开箱即用**，无需 pip 安装。

## 安装

三种方式，按需选择：

### 方式 A：独立二进制（零依赖，推荐给终端用户）

推 tag（`v*`）后 GitHub Actions 自动在 **Windows / macOS(arm64+x64) / Linux** 四个
runner 上用 PyInstaller 打包并挂到 Release，下载对应平台的单文件即可，**完全不需要
安装 Python**：

```bash
# macOS 下载后需去掉隔离标记（未签名二进制）
xattr -d com.apple.quarantine ockit-macos-arm64
chmod +x ockit-macos-arm64 && ./ockit-macos-arm64 --help
```

### 方式 B：脚本直跑（推荐给开发者，唯一源头）

```bash
python ockit.py --help      # 任何 Python 3.8+，无需 pip
# 或用包装脚本（自动寻找 py/python3/python，找不到时给出安装指引）
ockit.bat   # Windows
./ockit.sh  # macOS / Linux
```

平台差异说明：

| 平台 | 前提 |
|---|---|
| Linux | `python3` 几乎都预装，开箱即用 |
| macOS | 首次运行弹"安装命令行开发者工具"，确认后自动装好 python3 |
| Windows | 需装一次 Python：`winget install Python.Python.3.12`（wrapper 会自动提示） |

### 方式 C：本机自己打包

```bash
python -m pip install pyinstaller
pyinstaller --onefile --clean --name ockit ockit.py
```

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

## kext 上游数据库（内置）

Lilu / VirtualSMC 系 / AppleALC / NVMeFix / RestrictEvents / FeatureUnlock /
BrightnessKeys / VoodooI2C / AMFIPass / ECEnabler / IntelBluetoothFirmware(含 BlueToolFixup) /
NootedRed(weachy) / ForgedInvariant / SMCRadeonSensors / AMDRyzenCPUPowerManagement /
USBToolBox / AirportItlwm(laobamac fork, Sequoia/Tahoe 分段) / SimpleHDA(laobamac)。

不在库中的 kext（如本地自生成的 USBMap.kext）会被标记为 `local/custom` 并跳过上游检查。

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
