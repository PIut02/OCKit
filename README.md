# ockit — OpenCore EFI Toolbox (CLI)

A command-line equivalent of [OCAuxiliaryTools (OCAT)](https://github.com/ic005k/OCAuxiliaryTools), plus extensions of its own. Single-file Python, **standard library only** (Python 3.8+, no pip), runs on **Windows / macOS / Linux**.

[English](README.md) | [简体中文](README_cn.md)

## Why ockit

| Pain point in GUI tools | ockit's answer |
|---|---|
| Kext update checklist is compiled into the app, users cannot edit it | The upstream checklist lives in a **user-editable `KextChecklist.plist`** — override, add, remove or disable entries freely |
| Config edits by hand (error-prone Find/Replace copies) | Kernel patches are imported **verbatim** (`deepcopy`) — bytes stay byte-identical |
| No way to see *why* something was skipped | Fallbacks and skips are always **visible** (`local/custom`, upstream unreachable, etc.) |
| Chinese-network users struggle with GitHub access | Built-in **`GitHubMirror` / `GitHubApi`** settings (same idea as OCAT's mirror options) |

## Requirements

Python 3.8+ with **nothing else** — no pip packages, no virtualenv. Wrapper scripts locate `py`/`python3`/`python` and print a platform-specific install hint if no interpreter exists.

| Platform | Note |
|---|---|
| Linux | `python3` is preinstalled almost everywhere |
| macOS | First run may prompt to install Command Line Tools; python3 comes with it |
| Windows | Install once: `winget install Python.Python.3.12` |

Helper binaries (`macserial`, `ocvalidate`) are downloaded from official OpenCore releases on demand. On macOS, Gatekeeper may quarantine them:

```bash
xattr -d com.apple.quarantine ~/.ockit/cache/OpenCore-*/Utilities/**/macserial
```

## Commands

| Command | What it does |
|---|---|
| `info <config>` | Config summary: SMBIOS, boot-args, kext version table, patch stats |
| `validate <config>` | Built-in checks (missing files, duplicates, load order, MinKernel format, known-bad args) + optional official `ocvalidate` (`--download` fetches it) |
| `config get/set/rm` | Read/write any key by dot-path |
| `bootargs get/set/add/remove` | boot-args editing with risk hints for known flags |
| `acpi list/enable/disable/add/remove` | SSDT entry management |
| `kext list/check/install/update/remove` | Kext inventory, **upstream release comparison**, download + install/update (writes back to config, fixes load order); unknown/local kexts are marked `local/custom` |
| `patch list/import/enable/disable` | **Verbatim kernel-patch import** with Comment filter and MinKernel/MaxKernel override |
| `smbios show/gen` | SMBIOS generation via OC's official `macserial`, written back to config |
| `oc download/upgrade/resync` | OpenCore upgrade: swap binaries, diff against Sample.plist, `resync` fills in new keys |
| `diff a.plist b.plist` | Recursive config diff (`--schema` for keys-only) |
| `snapshot <efi>` | Rebuild ACPI/Add, Kernel/Add (incl. PlugIns sub-entries) and UEFI/Drivers from the directory — ProperTree "OC Clean Snapshot" equivalent |
| `backup <efi>` | Timestamped zip of the EFI |
| `check <config>` | Health report: validation + optional online kext check, markdown output |
| `checklist show/init/validate` | Manage the user-maintainable upstream checklist |
| `settings show/init/validate` | Manage ockit's own settings |

## Kext checklist: `KextChecklist.plist`

```bash
ockit checklist init          # write an editable template to the current directory
ockit checklist show          # effective checklist + its source
ockit kext check EFI --checklist my-list.plist
```

Entry fields: `Repo`, `Asset` (regex, **may be a list** for upstreams shipping several zips), `Provides`, `Prerelease`, `MinKernel`/`MaxKernel`, `Alias`.

Merge semantics (user file vs built-in defaults):

| Action | How |
|---|---|
| Override an entry (e.g. switch to a mirror) | Same key, only the fields you change |
| Add an entry | A new key |
| Remove built-in entries | Top-level `Remove` array |
| Disable an entry | `Disabled = true` |
| Comment | Any key starting with `#` |

Lookup order: `--checklist` > env `OCKIT_CHECKLIST` > cwd > script dir > `~/.ockit/` > built-in. `kext check` always prints which source is in effect. The built-in list ships ~19 entries (Lilu, VirtualSMC family, AppleALC, AMFIPass, IntelBluetoothFirmware, NootedRed, USBToolBox, laobamac's AirportItlwm/SimpleHDA, …). Known limitation: for multi-kext bundles (e.g. BlueToolFixup inside the IntelBluetoothFirmware zip) the version compare uses the bundle's release tag.

`GITHUB_TOKEN` / `GH_TOKEN` env vars (or the `GitHubToken` setting) raise the API rate limit — recommended, anonymous quota is 60 requests/hour.

## Settings: `OckitSettings.plist`

```bash
ockit settings init && ockit settings show
```

| Key | Purpose | Default |
|---|---|---|
| `DefaultEFI` | Default EFI dir for kext/snapshot/backup/oc | — |
| `DefaultConfig` | Default config.plist for validate/check | — |
| `Checklist` | Extra lookup location for KextChecklist.plist | — |
| `BackupDir` | Backup output dir | alongside EFI |
| `GitHubToken` | GitHub API token (env vars win) | — |
| `GitHubApi` | JSON API base (mirror services proxying the REST API) | `https://api.github.com` |
| `GitHubMirror` | **Download URL rewriter** (release assets, OpenCore zips); `{url}` placeholder or plain prefix | — |
| `Timeout` / `Retries` | Network timeout / retry count | 30 / 3 |
| `Prerelease` | Accept prerelease kexts by default | false |
| `Color` | ANSI colors | true |

Same lookup order as the checklist (`--settings` global flag > env `OCKIT_SETTINGS` > cwd > script dir > `~/.ockit/` > defaults). With `DefaultEFI` set, `ockit validate` / `ockit kext list` need no arguments.

### GitHub mirror (for restricted networks)

```xml
<!-- prefix style (ghproxy-family mirrors) -->
<key>GitHubMirror</key>
<string>https://ghproxy.net/</string>

<!-- or {url} placeholder style -->
<key>GitHubMirror</key>
<string>https://mirror.example.com/?u={url}</string>
```

Mirrored downloads always print the rewritten URL. Mirrors rewrite **downloads**; API rate limiting is better solved by `GitHubToken`.

## Typical workflow: upgrading a Hackintosh EFI

```bash
ockit backup /Volumes/EFI/EFI
ockit check EFI/OC/config.plist --net --markdown report.md
ockit oc upgrade /Volumes/EFI/EFI
ockit oc resync /Volumes/EFI/EFI/OC/config.plist
ockit patch import EFI/OC/config.plist ref-config.plist --match "26.4" --force
ockit kext update EFI
ockit validate EFI/OC/config.plist --download
```

## Design principles

1. **Verbatim first** — patches/configs are moved with `deepcopy`; hand-copied Find/Replace is how configs get bricked.
2. **Machine-check everything** — run `validate` (built-in rules + official `ocvalidate`) after any write.
3. **Order is semantics** — Lilu first, VirtualSMC second, plugins right after their parent; snapshot/install fix order automatically.
4. **Fallbacks are visible** — nothing is skipped silently.

## Testing

```bash
python -m unittest discover -s tests
```

31 offline unit tests cover plist I/O (format & key-order preservation), dot-path access, boot-args ops, patch import (bytes fidelity, dedupe, version override), kext ordering, snapshot rebuild, the validator and settings/checklist merge semantics.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with the Acidanthera team or OCAT; kexts and OpenCore are downloaded from their official upstream releases.

## Acknowledgements

- [OCAuxiliaryTools](https://github.com/ic005k/OCAuxiliaryTools) — the GUI tool whose workflow ockit mirrors
- [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) / Acidanthera — OpenCore, `macserial`, `ocvalidate`, Lilu & friends
- [ProperTree](https://github.com/corpnewt/ProperTree) — the snapshot semantics ockit follows
- All upstream kext authors referenced in the built-in checklist
