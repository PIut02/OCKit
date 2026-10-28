# Changelog

本仓库遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

## 0.1.0 (2026-10-27)

首个可用版本。

### Added
- `info` / `validate`（内置规则 + 官方 ocvalidate 集成）
- `config` / `bootargs` / `acpi` / `patch`（verbatim 导入）配置操作
- `kext` list/check/install/update/remove，内置 19 项上游 Release 数据库
- `checklist` show/init/validate：**kext 上游清单独立为 KextChecklist.plist**，
  用户可覆盖/新增/删除/停用条目（解决 OCAT 清单不可改的弊端）；
  Asset 支持数组以适配上游多 zip 分装
- `settings` show/init/validate：**ockit 自身设置独立为 OckitSettings.plist**，
  可配置 DefaultEFI/DefaultConfig/Checklist/BackupDir/GitHubToken/Timeout/
  Retries/Prerelease/Color；kext/snapshot/oc/backup 的 EFI 参数与 validate/check 的
  config 参数改为可选并支持设置回退
- GitHub 镜像支持（对标 OCAT）：`GitHubMirror` 重写下载 URL（`{url}` 占位符或
  纯前缀两种写法，下载时打印实际地址），`GitHubApi` 可替换 JSON API 基址
- `smbios show/gen`（macserial 集成）
- `oc download/upgrade/resync`（OpenCore 升级 + Sample.plist 键位同步）
- `diff`（递归/仅键位）、`snapshot`（EFI 快照重建，含 PlugIns 子条目与内核段推断）
- `backup` / `check`（体检报告，可输出 markdown）
- 单元测试 31 项（离线逻辑全覆盖）
- 分发策略：仅 Python 脚本（stdlib-only），环境由使用者自行准备；
  launcher 负责寻找解释器并给出平台安装指引

### Fixed
- 内置清单三处上游失效：AMFIPass 原仓库（dhinakg）已删除，改 kaoskinkae 镜像；
  IntelBluetoothFirmware 资产名无 -RELEASE 后缀；AMDRyzenCPUPowerManagement
  上游分装两个 zip（Asset 数组支持）

### Removed
- （曾短暂加入）GitHub Actions 三平台二进制打包工作流，随"仅脚本分发"决策移除
