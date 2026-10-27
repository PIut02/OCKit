# -*- coding: utf-8 -*-
"""ockit 单元测试（仅覆盖离线逻辑；网络相关命令用 mock/跳过）"""

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


class KextDbTest(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(ockit.kext_db_lookup("Lilu.kext")[0], "Lilu")
        self.assertEqual(ockit.kext_db_lookup("BlueToolFixup.kext")[0],
                         "IntelBluetoothFirmware")
        self.assertIsNone(ockit.kext_db_lookup("AirportItlwm.kext"))
        self.assertIsNone(ockit.kext_db_lookup("USBMap.kext"))  # 本地自生成件


if __name__ == "__main__":
    unittest.main()
