# -*- coding: utf-8 -*-
"""ockit 单元测试（仅覆盖离线逻辑；网络相关命令用 mock/跳过）"""

import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ockit  # noqa: E402


def sample_config():
    """构造一份与真实 config 同构的最小配置。"""
    guid = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
    return {
        "ACPI": {"Add": [
            {"Comment": "", "Enabled": True, "Path": "SSDT-EC.aml"},
            {"Comment": "", "Enabled": False, "Path": "SSDT-Gone.aml"},
        ]},
        "Booter": {"Quirks": {"SetupVirtualMap": True}},
        "DeviceProperties": {"Add": {"PciRoot(0x0)/Pci(0x8,0x1)/Pci(0x0,0x6)":
                                     {"layout-id": 33}}},
        "Kernel": {
            "Add": [
                {"Arch": "x86_64", "BundlePath": "AppleALC.kext", "Comment": "",
                 "Enabled": True, "ExecutablePath": "Contents/MacOS/AppleALC",
                 "MaxKernel": "", "MinKernel": "", "PlistPath": "Contents/Info.plist"},
                {"Arch": "x86_64", "BundlePath": "Lilu.kext", "Comment": "",
                 "Enabled": True, "ExecutablePath": "Contents/MacOS/Lilu",
                 "MaxKernel": "", "MinKernel": "", "PlistPath": "Contents/Info.plist"},
                {"Arch": "x86_64", "BundlePath": "VoodooI2C.kext", "Comment": "",
                 "Enabled": True, "ExecutablePath": "Contents/MacOS/VoodooI2C",
                 "MaxKernel": "", "MinKernel": "", "PlistPath": "Contents/Info.plist"},
                {"Arch": "x86_64",
                 "BundlePath": "VoodooI2C.kext/Contents/PlugIns/VoodooInput.kext",
                 "Comment": "", "Enabled": True,
                 "ExecutablePath": "Contents/MacOS/VoodooInput",
                 "MaxKernel": "", "MinKernel": "", "PlistPath": "Contents/Info.plist"},
            ],
            "Patch": [
                {"Comment": "algrey | test | 13.3+", "Enabled": True,
                 "Find": b"\x00\x01", "Replace": b"\x00\x02",
                 "MaxKernel": "", "MinKernel": "13.3"},
            ],
            "Quirks": {"PanicNoKextDump": True},
        },
        "Misc": {"Security": {"SecureBootModel": "Disabled"}},
        "NVRAM": {"Add": {guid: {
            "boot-args": "keepsyms=1 -bfixup debug=0x100",
            "csr-active-config": b"\x00\x00\x00\x00"}}},
        "PlatformInfo": {"Generic": {"SystemProductName": "MacBookPro16,3"}},
        "UEFI": {"Drivers": [
            {"Comment": "", "Enabled": True, "Path": "OpenRuntime.efi"},
            {"Comment": "", "Enabled": True, "Path": "Missing.efi"},
        ]},
    }


class PlistIOTest(unittest.TestCase):
    def test_roundtrip_binary_and_xml(self):
        cfg = sample_config()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.plist"
            ockit.save_plist(cfg, p, plistlib.FMT_BINARY)
            loaded, fmt = ockit.load_plist(p)
            self.assertEqual(fmt, plistlib.FMT_BINARY)
            self.assertEqual(loaded["Kernel"]["Patch"][0]["Find"], b"\x00\x01")
            ockit.save_plist(loaded, p, fmt)
            loaded2, _ = ockit.load_plist(p)
            self.assertEqual(loaded2["NVRAM"]["Add"][list(loaded2["NVRAM"]["Add"])[0]]
                             ["boot-args"], "keepsyms=1 -bfixup debug=0x100")

    def test_key_order_preserved(self):
        cfg = sample_config()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.xml"
            ockit.save_plist(cfg, p, plistlib.FMT_XML)
            text = p.read_text(encoding="utf-8")
            self.assertLess(text.index("Kernel"), text.index("PlatformInfo"))


class DotPathTest(unittest.TestCase):
    def test_get_set_rm(self):
        cfg = sample_config()
        self.assertEqual(ockit.cfg_get(cfg, "PlatformInfo.Generic.SystemProductName"),
                         "MacBookPro16,3")
        self.assertIsNone(ockit.cfg_get(cfg, "No.Such.Path"))
        ockit.cfg_set(cfg, "Misc.Security.Vault", "Basic")
        self.assertEqual(ockit.cfg_get(cfg, "Misc.Security.Vault"), "Basic")
        old = ockit.cfg_rm(cfg, "Misc.Security.Vault")
        self.assertEqual(old, "Basic")


class BootArgsTest(unittest.TestCase):
    def test_add_remove_replace(self):
        cfg = sample_config()
        ockit.bootargs_modify(cfg, add=["-wegnoegpu", "itlwm_he=1"])
        args = ockit.bootargs_get(cfg).split()
        self.assertIn("-wegnoegpu", args)
        self.assertIn("itlwm_he=1", args)
        ockit.bootargs_modify(cfg, add=["itlwm_he=0"])  # 同 key 覆盖
        args = ockit.bootargs_get(cfg).split()
        self.assertIn("itlwm_he=0", args)
        self.assertNotIn("itlwm_he=1", args)
        ockit.bootargs_modify(cfg, remove=["debug=0x100", "-wegnoegpu"])
        args = ockit.bootargs_get(cfg).split()
        self.assertNotIn("debug=0x100", args)
        self.assertNotIn("-wegnoegpu", args)


class OrderTest(unittest.TestCase):
    def test_lilu_first_and_plugin_after_parent(self):
        entries = sample_config()["Kernel"]["Add"]
        out = ockit.sort_kext_entries(entries)
        paths = [e["BundlePath"] for e in out]
        self.assertEqual(ockit._bundle_base(paths[0]), "Lilu.kext")
        self.assertLess(paths.index("Lilu.kext"), paths.index("AppleALC.kext"))
        self.assertLess(paths.index("VoodooI2C.kext"),
                        paths.index("VoodooI2C.kext/Contents/PlugIns/VoodooInput.kext"))

    def test_score(self):
        self.assertEqual(ockit.order_score("Lilu.kext"), 0)
        self.assertEqual(ockit.order_score("AppleALC.kext"), 3)  # Lilu 插件在 VirtualSMC 之后
        self.assertLess(ockit.order_score("VirtualSMC.kext"),
                        ockit.order_score("AppleALC.kext"))
        self.assertLess(ockit.order_score("VoodooI2C.kext"),
                        ockit.order_score("VoodooI2C.kext/Contents/PlugIns/VoodooInput.kext"))


class PatchImportTest(unittest.TestCase):
    def _run_import(self, src_cfg, match=None, min_=None, max_=None, enable=None, force=False):
        cfg = sample_config()
        imported, skipped = ockit.import_patches(
            cfg, src_cfg, match=match, min_=min_, max_=max_, enable=enable, force=force)
        return cfg["Kernel"]["Patch"], imported, skipped

    def test_import_match_and_override(self):
        src = [
            {"Comment": "laobamac | non-monotonic | 26.4+", "Enabled": True,
             "Find": b"\xaa", "Replace": b"\xbb", "MinKernel": "26.4", "MaxKernel": ""},
            {"Comment": "algrey | PAT | 15.0+", "Enabled": True,
             "Find": b"\xcc", "Replace": b"\xdd", "MinKernel": "15.0", "MaxKernel": ""},
        ]
        patches, imported, _ = self._run_import(src, match="26.4", min_="26.4.0")
        self.assertEqual(imported, 1)
        new = [p for p in patches if p["Comment"] == "laobamac | non-monotonic | 26.4+"]
        self.assertEqual(new[0]["MinKernel"], "26.4.0")
        self.assertEqual(new[0]["Find"], b"\xaa")  # bytes 原样保留
        self.assertEqual(len(patches), 2)

    def test_import_dedupe_by_comment(self):
        src = [{"Comment": "algrey | test | 13.3+", "Enabled": True,
                "Find": b"\x01", "Replace": b"\x02", "MinKernel": "", "MaxKernel": ""}]
        patches, imported, skipped = self._run_import(src)
        self.assertEqual(imported, 0)     # Comment 已存在
        self.assertEqual(skipped, 1)
        self.assertEqual(len(patches), 1)


class DiffTest(unittest.TestCase):
    def test_missing_and_extra(self):
        a = {"Kernel": {"Add": [], "Quirks": {"NewQuirk": True, "Same": 1}}}
        b = {"Kernel": {"Add": [], "Quirks": {"Same": 1, "Legacy": 2}}}
        out = []
        ockit.report_diff(a, b, out=out.append)
        text = "\n".join(out)
        self.assertIn("[缺失] Kernel.Quirks.NewQuirk", text)
        self.assertIn("[多余] Kernel.Quirks.Legacy", text)

    def test_resync_adds_missing_only(self):
        a = {"Kernel": {"Quirks": {"NewQuirk": True, "Same": 1}}}
        b = {"Kernel": {"Quirks": {"Same": 1}}}
        added = ockit.resync_cfg(a, b)
        self.assertEqual(added, 1)
        self.assertTrue(b["Kernel"]["Quirks"]["NewQuirk"])
        # 已有值不被覆盖
        b2 = {"Kernel": {"Quirks": {"Same": 99}}}
        a2 = {"Kernel": {"Quirks": {"Same": 1}}}
        added = ockit.resync_cfg(a2, b2)
        self.assertEqual(added, 0)
        self.assertEqual(b2["Kernel"]["Quirks"]["Same"], 99)


class KextEntryTest(unittest.TestCase):
    def _make_kext(self, root, name, exe=True, plugins=()):
        kdir = Path(root) / name
        (kdir / "Contents" / "MacOS").mkdir(parents=True)
        info = {"CFBundleIdentifier": "test." + name, "CFBundleShortVersionString": "1.2.3"}
        if exe:
            info["CFBundleExecutable"] = name[:-5]
            (kdir / "Contents" / "MacOS" / (name[:-5])).write_bytes(b"bin")
        (kdir / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps(info, fmt=plistlib.FMT_XML))
        for pl in plugins:
            self._make_kext(kdir / "Contents" / "PlugIns", pl)
        return kdir

    def test_make_kext_entry(self):
        with tempfile.TemporaryDirectory() as td:
            self._make_kext(td, "Fake.kext")
            entry = ockit.make_kext_entry(Path(td) / "Fake.kext", minkernel="24.2.0")
            self.assertEqual(entry["BundlePath"], "Fake.kext")
            self.assertEqual(entry["ExecutablePath"], "Contents/MacOS/Fake")
            self.assertEqual(entry["MinKernel"], "24.2.0")
            self.assertEqual(ockit.read_kext_version(Path(td) / "Fake.kext"), "1.2.3")

    def test_snapshot_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            efi = Path(td) / "EFI"
            (efi / "OC" / "Kexts").mkdir(parents=True)
            (efi / "OC" / "ACPI").mkdir()
            (efi / "OC" / "Drivers").mkdir()
            self._make_kext(efi / "OC" / "Kexts", "Lilu.kext")
            self._make_kext(efi / "OC" / "Kexts", "VoodooI2C.kext",
                            plugins=["VoodooInput.kext"])
            (efi / "OC" / "ACPI" / "SSDT-EC.aml").write_bytes(b"\x00")
            (efi / "OC" / "Drivers" / "OpenRuntime.efi").write_bytes(b"\x00")
            cfg = sample_config()
            cfg_path = efi / "OC" / "config.plist"
            ockit.save_plist(cfg, cfg_path, plistlib.FMT_XML)

            ns = ockit.build_parser().parse_args(
                ["snapshot", str(efi), "--dry-run"])
            # dry-run 不落盘
            ns2 = ockit.build_parser().parse_args(["snapshot", str(efi)])
            self.assertEqual(ns2.func(ns2), 0)
            rebuilt, _ = ockit.load_plist(cfg_path)
            bundles = [e["BundlePath"] for e in rebuilt["Kernel"]["Add"]]
            self.assertIn("Lilu.kext", bundles)
            self.assertIn("VoodooI2C.kext", bundles)
            self.assertIn("VoodooI2C.kext/Contents/PlugIns/VoodooInput.kext", bundles)
            self.assertEqual(bundles[0], "Lilu.kext")
            paths = [a["Path"] for a in rebuilt["ACPI"]["Add"]]
            self.assertEqual(paths, ["SSDT-EC.aml"])
            drv = [d["Path"] for d in rebuilt["UEFI"]["Drivers"]]
            self.assertEqual(drv, ["OpenRuntime.efi"])


class ValidateTest(unittest.TestCase):
    def test_validate_finds_problems(self):
        cfg = sample_config()  # SSDT-Gone.aml / Missing.efi 故意缺失
        out = []
        problems, warnings = ockit.validate_config(cfg, Path("/nonexistent"), out=out.append)
        text = "\n".join(out)
        self.assertTrue(any("SSDT-Gone.aml" in p for p in problems))
        self.assertTrue(any("Missing.efi" in p for p in problems))
        self.assertTrue(any("MacBookPro16,3" in w for w in warnings))
        self.assertTrue(any("-bfixup" in w for w in warnings))

    def test_min_max_kernel_format(self):
        cfg = sample_config()
        cfg["Kernel"]["Add"][0]["MinKernel"] = "24.2.0"
        cfg["Kernel"]["Add"][0]["MaxKernel"] = "bad"
        problems, _ = ockit.validate_config(cfg, Path("/nonexistent"), out=lambda s: None)
        self.assertTrue(any("MaxKernel 格式非法" in p for p in problems))


class ChecklistTest(unittest.TestCase):
    """kext checklist：独立 plist 维护 / 合并 / 解析顺序"""

    def test_merge_override_add_remove_disable(self):
        base = {
            "Lilu": {"repo": "a/Lilu", "asset": r"Lilu-\d+-RELEASE\.zip", "provides": ["Lilu.kext"]},
            "Old": {"repo": "a/Old", "asset": r"O-\d+\.zip", "provides": ["Old.kext"]},
        }
        user = {
            "#comment": "hi",
            "Lilu": {"Repo": "my-fork/Lilu", "MinKernel": "20.0.0"},   # 覆盖
            "Mine": {"Repo": "me/Mine", "Asset": r"M-.*\.zip", "Provides": ["Mine.kext"]},  # 新增
            "Remove": ["Old"],                                          # 删除
            "Gone": {"Repo": "x/G", "Asset": "g.zip", "Provides": ["G.kext"], "Disabled": True},
        }
        db = ockit.merge_checklist(base, user)
        self.assertEqual(db["Lilu"]["repo"], "my-fork/Lilu")
        self.assertEqual(db["Lilu"]["asset"], r"Lilu-\d+-RELEASE\.zip")  # 未覆盖字段保留
        self.assertEqual(db["Lilu"]["minkernel"], "20.0.0")              # 键名大小写归一
        self.assertIn("Mine", db)
        self.assertNotIn("Old", db)     # Remove 生效
        self.assertNotIn("Gone", db)    # Disabled 生效

    def test_resolution_order_and_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            custom = Path(td) / "My.plist"
            user = {"MyKext": {"Repo": "me/MyKext", "Asset": r"My-.*\.zip",
                               "Provides": ["MyKext.kext"]}}
            custom.write_bytes(plistlib.dumps(user, fmt=plistlib.FMT_XML))
            db, src = ockit.load_checklist(str(custom))
            self.assertIn("MyKext", db)
            self.assertIn("Lilu", db)  # 内置默认仍在
            self.assertEqual(src, str(custom))

            ockit.set_checklist(str(custom))
            self.assertEqual(ockit.kext_db_lookup("MyKext.kext")[0], "MyKext")
            self.assertEqual(ockit.kext_db_lookup("Lilu.kext")[0], "Lilu")
            # 还原，避免影响其他测试
            ockit._CHECKLIST_STATE.update({"db": None, "src": None})

    def test_env_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Env.plist"
            p.write_bytes(plistlib.dumps({"EnvOnly": {"Repo": "e/E", "Asset": "x.zip",
                                                      "Provides": ["EnvOnly.kext"]}},
                                         fmt=plistlib.FMT_XML))
            old = os.environ.get("OCKIT_CHECKLIST")
            os.environ["OCKIT_CHECKLIST"] = str(p)
            try:
                db, src = ockit.load_checklist(None)
                self.assertIn("EnvOnly", db)
                self.assertEqual(src, str(p))
            finally:
                if old is None:
                    os.environ.pop("OCKIT_CHECKLIST", None)
                else:
                    os.environ["OCKIT_CHECKLIST"] = old
            ockit._CHECKLIST_STATE.update({"db": None, "src": None})

    def test_shipped_template_matches_builtin(self):
        tpl = Path(ockit.__file__).resolve().parent / ockit.CHECKLIST_FILENAME
        if not tpl.exists():
            self.skipTest("KextChecklist.plist 未随仓库提交")
        with open(tpl, "rb") as f:
            data = plistlib.load(f)
        names = {k for k in data if not str(k).startswith("#") and k != "Remove"}
        self.assertEqual(names, set(ockit.DEFAULT_CHECKLIST))


class SettingsTest(unittest.TestCase):
    """ockit 自身设置：OckitSettings.plist 的合并/解析/回退"""

    def setUp(self):
        ockit._SETTINGS.update({"loaded": False, "src": None,
                                "data": dict(ockit.DEFAULT_SETTINGS)})
        self._old_env = os.environ.pop("OCKIT_SETTINGS", None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["OCKIT_SETTINGS"] = self._old_env
        else:
            os.environ.pop("OCKIT_SETTINGS", None)
        ockit._SETTINGS.update({"loaded": False, "src": None,
                                "data": dict(ockit.DEFAULT_SETTINGS)})

    def test_defaults_when_no_file(self):
        data, src = ockit.load_settings(None)
        self.assertEqual(data["Timeout"], 30)
        self.assertIn("builtin-default", src)

    def test_explicit_file_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "S.plist"
            user = {"Timeout": 7, "Retries": 1, "DefaultEFI": "E:/EFI",
                    "Prerelease": True, "#note": "注释"}
            p.write_bytes(plistlib.dumps(user, fmt=plistlib.FMT_XML))
            data, src = ockit.load_settings(str(p))
            self.assertEqual(data["Timeout"], 7)
            self.assertEqual(data["Retries"], 1)
            self.assertEqual(data["DefaultEFI"], "E:/EFI")
            self.assertTrue(data["Prerelease"])
            self.assertEqual(data["Color"], True)  # 未覆盖字段继承默认
            self.assertEqual(src, str(p))

    def test_env_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Env.plist"
            p.write_bytes(plistlib.dumps({"Timeout": 99}, fmt=plistlib.FMT_XML))
            os.environ["OCKIT_SETTINGS"] = str(p)
            data, src = ockit.load_settings(None)
            self.assertEqual(data["Timeout"], 99)
            self.assertEqual(src, str(p))

    def test_token_in_gh_headers(self):
        ockit._SETTINGS["data"]["GitHubToken"] = "ghp_testtoken"
        ockit._SETTINGS["loaded"] = True
        h = ockit._gh_headers()
        self.assertEqual(h.get("Authorization"), "Bearer ghp_testtoken")
        # 环境变量优先于 settings
        os.environ["GITHUB_TOKEN"] = "envtoken"
        try:
            h = ockit._gh_headers()
            self.assertEqual(h.get("Authorization"), "Bearer envtoken")
        finally:
            os.environ.pop("GITHUB_TOKEN", None)

    def test_resolve_efi_and_config(self):
        ns_efi = argparse_ns = type("NS", (), {"efi": None, "config": None})()
        with self.assertRaises(ockit.ToolError):
            ockit.resolve_efi(None)
        ockit._SETTINGS["data"]["DefaultEFI"] = "E:/myEFI"
        ockit._SETTINGS["loaded"] = True
        self.assertEqual(ockit.resolve_efi(None), Path("E:/myEFI"))
        self.assertEqual(ockit.resolve_efi("X:/other"), Path("X:/other"))
        self.assertEqual(ockit.resolve_config(None, Path("E:/myEFI")),
                         Path("E:/myEFI/OC/config.plist"))
        ockit._SETTINGS["data"]["DefaultConfig"] = "C:/cfg.plist"
        self.assertEqual(ockit.resolve_config(None, None), Path("C:/cfg.plist"))
        del argparse_ns  # 未使用，防误读

    def test_checklist_via_settings(self):
        with tempfile.TemporaryDirectory() as td:
            ck = Path(td) / "Custom.plist"
            ck.write_bytes(plistlib.dumps(
                {"ViaSettings": {"Repo": "s/S", "Asset": "a.zip",
                                 "Provides": ["ViaSettings.kext"]}},
                fmt=plistlib.FMT_XML))
            st = Path(td) / "S.plist"
            st.write_bytes(plistlib.dumps({"Checklist": str(ck)}, fmt=plistlib.FMT_XML))
            ockit.load_settings(str(st))
            db, src = ockit.load_checklist(None)
            self.assertIn("ViaSettings", db)
            self.assertEqual(src, str(ck))
            ockit._CHECKLIST_STATE.update({"db": None, "src": None})

    def test_settings_init_and_validate_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "OckitSettings.plist"
            ns = type("NS", (), {"action": "init", "path": str(p), "force": False})()
            self.assertEqual(ockit.cmd_settings(ns), 0)
            with open(p, "rb") as f:
                data = plistlib.load(f)
            self.assertIn("#comment", data)
            self.assertEqual(data["Timeout"], ockit.DEFAULT_SETTINGS["Timeout"])
            ns2 = type("NS", (), {"action": "validate", "path": str(p)})()
            self.assertEqual(ockit.cmd_settings(ns2), 0)
            # 非法类型
            bad = Path(td) / "Bad.plist"
            bad.write_bytes(plistlib.dumps({"Timeout": "not-int"}, fmt=plistlib.FMT_XML))
            ns3 = type("NS", (), {"action": "validate", "path": str(bad)})()
            with self.assertRaises(ockit.ToolError):
                ockit.cmd_settings(ns3)


class KextDbTest(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(ockit.kext_db_lookup("Lilu.kext")[0], "Lilu")
        self.assertEqual(ockit.kext_db_lookup("BlueToolFixup.kext")[0],
                         "IntelBluetoothFirmware")
        self.assertIsNone(ockit.kext_db_lookup("AirportItlwm.kext"))
        self.assertIsNone(ockit.kext_db_lookup("USBMap.kext"))  # 本地自生成件


class GitHubMirrorTest(unittest.TestCase):
    """GitHub API 基址与下载镜像配置"""

    def setUp(self):
        ockit._SETTINGS.update({"loaded": False, "src": None,
                                "data": dict(ockit.DEFAULT_SETTINGS)})

    def tearDown(self):
        ockit._SETTINGS.update({"loaded": False, "src": None,
                                "data": dict(ockit.DEFAULT_SETTINGS)})

    def test_api_url_default_and_override(self):
        self.assertEqual(ockit.api_url("/repos/a/b"), "https://api.github.com/repos/a/b")
        ockit._SETTINGS["data"]["GitHubApi"] = "https://ghapi.example.com/"
        ockit._SETTINGS["loaded"] = True
        self.assertEqual(ockit.api_url("/repos/a/b"), "https://ghapi.example.com/repos/a/b")

    def test_apply_mirror_off_by_default(self):
        url = "https://github.com/a/b/releases/download/v1/x.zip"
        self.assertEqual(ockit.apply_mirror(url), url)

    def test_apply_mirror_prefix_style(self):
        ockit._SETTINGS["data"]["GitHubMirror"] = "https://ghproxy.net/"
        ockit._SETTINGS["loaded"] = True
        url = "https://github.com/a/b/releases/download/v1/x.zip"
        self.assertEqual(ockit.apply_mirror(url),
                         "https://ghproxy.net/https://github.com/a/b/releases/download/v1/x.zip")

    def test_apply_mirror_url_template_style(self):
        ockit._SETTINGS["data"]["GitHubMirror"] = "https://mirror.example.com/?u={url}"
        ockit._SETTINGS["loaded"] = True
        url = "https://github.com/a/b/releases/download/v1/x.zip"
        self.assertEqual(ockit.apply_mirror(url),
                         "https://mirror.example.com/?u=https://github.com/a/b/releases/download/v1/x.zip")
        # 显式传参覆盖 settings
        self.assertEqual(ockit.apply_mirror(url, ""), url)


class GitHubMirrorSettingsTest(unittest.TestCase):
    def test_settings_init_contains_mirror_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "S.plist"
            ns = type("NS", (), {"action": "init", "path": str(p), "force": False})()
            ockit.cmd_settings(ns)
            with open(p, "rb") as f:
                data = plistlib.load(f)
            self.assertIn("GitHubApi", data)
            self.assertIn("GitHubMirror", data)


if __name__ == "__main__":
    unittest.main()
