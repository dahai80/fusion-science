#!/usr/bin/env python3
"""Check environment for fusion-science domestic research deployment.

Verifies dependencies, mirror configuration, and offline mode.
Usage:
    python scripts/check_env.py
"""

import importlib
import os
import platform
import sys
from pathlib import Path


def check_dependency(name: str, package: str = "") -> tuple[bool, str]:
    """Check if a Python dependency is available."""
    try:
        mod = importlib.import_module(package or name)
        version = getattr(mod, "__version__", "installed")
        return True, str(version)
    except ImportError:
        return False, "NOT INSTALLED"


def main():
    errors = 0
    padding = 30

    print("=" * 60)
    print("🔬 Fusion-Science 环境校验")
    print(f"   Python: {sys.version.split()[0]}")
    print(f"   Platform: {platform.platform()}")
    print(f"   Path: {Path(__file__).resolve().parent.parent}")
    print("=" * 60)

    # ---- 核心依赖 ----
    print("\n📦 核心依赖检查:")
    core_deps = [
        ("mlx", "mlx"),
        ("numpy", "numpy"),
        ("Bio", "Bio"),
        ("rdkit", "rdkit"),
        ("httpx", "httpx"),
        ("pydantic", "pydantic"),
        ("click", "click"),
        ("matplotlib", "matplotlib"),
    ]
    for name, pkg in core_deps:
        ok, ver = check_dependency(name, pkg)
        status = "✅" if ok else "❌"
        print(f"  {status} {name:<{padding}} {ver}")
        if not ok:
            errors += 1

    # ---- 可选依赖 ----
    print("\n🔧 可选依赖检查:")
    opt_deps = [
        ("py3Dmol", "py3Dmol"),
        ("rpy2", "rpy2"),
        ("jupyter_client", "jupyter_client"),
        ("seaborn", "seaborn"),
        ("pyyaml", "yaml"),
    ]
    for name, pkg in opt_deps:
        ok, ver = check_dependency(name, pkg)
        status = "✅" if ok else "⬜"
        print(f"  {status} {name:<{padding}} {ver}")

    # ---- 环境变量 ----
    print("\n🌐 环境变量检查:")
    env_vars = [
        "FUSION_OFFLINE_MODE",
        "FUSION_MODEL_HUB",
        "FUSION_SCIENCE_USE_MIRRORS",
        "FUSION_SCIENCE_CACHE_ENABLED",
        "FUSION_SCI_PUBMED_MIRROR",
        "FUSION_SCI_PDB_MIRROR",
        "FUSION_SCI_UNIPROT_MIRROR",
        "FUSION_SCI_ENSEMBL_MIRROR",
        "FUSION_SCI_CHEMBL_MIRROR",
        "FUSION_SCI_NGDC_URL",
        "FUSION_SCI_CNGB_URL",
        "FUSION_SCI_CNKI_URL",
        "FUSION_SCI_ScienceDB_URL",
    ]
    for var in env_vars:
        val = os.getenv(var, "")
        if val:
            print(f"  ✅ {var:<{padding}} {val[:60]}")
        else:
            print(f"  ⚠️  {var:<{padding}} (未设置)")
            # Not counting as error, but worth noting

    # ---- 国内数据库可访问性 ----
    print("\n📡 国内科研数据库可达性 (ping):")
    domestic_urls = [
        ("NGDC", os.getenv("FUSION_SCI_NGDC_URL", "https://ngdc.cncb.ac.cn")),
        ("CNGB", os.getenv("FUSION_SCI_CNGB_URL", "https://www.cngb.org")),
        ("ScienceDB", os.getenv("FUSION_SCI_ScienceDB_URL", "https://www.scidb.cn")),
    ]
    import urllib.request
    import urllib.error

    for name, url in domestic_urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=5)
            print(f"  ✅ {name:<{padding}} {resp.status} {url}")
        except Exception as e:
            print(f"  ⚠️  {name:<{padding}} 不可达 ({e})")

    # ---- model-hub 目录 ----
    print("\n📁 fusion-model-hub 目录:")
    hub = os.getenv("FUSION_MODEL_HUB", str(Path.home() / "fusion-workspace/fusion-model-hub"))
    hub_path = Path(hub)
    if hub_path.exists():
        print(f"  ✅ 根目录: {hub_path}")
        for sub in ["models", "datasets", "evals", "snapshots", "cache"]:
            sub_path = hub_path / sub
            if sub_path.exists():
                print(f"     ✅ {sub}/")
            else:
                print(f"     ⚠️  {sub}/ (不存在)")
    else:
        print(f"  ⚠️  {hub_path} (不存在, 运行 deploy/setup_env.sh 创建)")

    # ---- 离线模式 ----
    print("\n🔒 离线模式状态:")
    offline = os.getenv("FUSION_OFFLINE_MODE", "false")
    if offline.lower() == "true":
        print("  ✅ 已启用 — 所有境外接口已关闭，仅使用本地缓存 + 国内镜像")
    else:
        print("  ⚠️  未启用 — 可访问境外接口，部分功能可能受限")

    # ---- 总结 ----
    print("\n" + "=" * 60)
    if errors == 0:
        print("✅ 环境验证通过，可离线使用 fusion-science")
    else:
        print(f"⚠️  发现 {errors} 个依赖缺失，请运行:")
        print(f"   pip install -e \"{Path(__file__).resolve().parent.parent}\"")
    print("=" * 60)
    return errors


if __name__ == "__main__":
    sys.exit(main())