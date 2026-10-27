# Changelog

本仓库遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

## 0.1.0 (2026-10-27)

首个可用版本。

### Added
- `info` / `validate`（内置规则 + 官方 ocvalidate 集成）
- `config` / `bootargs` / `acpi` / `patch`（verbatim 导入）配置操作
- `kext` list/check/install/update/remove，内置 19 项上游 Release 数据库
- `smbios show/gen`（macserial 集成）
- `oc download/upgrade/resync`（OpenCore 升级 + Sample.plist 键位同步）
- `diff`（递归/仅键位）、`snapshot`（EFI 快照重建，含 PlugIns 子条目与内核段推断）
- `backup` / `check`（体检报告，可输出 markdown）
- 单元测试 15 项（离线逻辑全覆盖）
- 分发策略：仅 Python 脚本（stdlib-only），环境由使用者自行准备；
  launcher 负责寻找解释器并给出平台安装指引

### Removed
- （曾短暂加入）GitHub Actions 三平台二进制打包工作流，随"仅脚本分发"决策移除
