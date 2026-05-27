#!/usr/bin/env python3
"""
Memento — 记忆 + 知识库系统 配置引导
===========================================
第一次使用前请运行此脚本。

用法:
    python3 setup.py         交互式配置
    python3 setup.py --help  查看选项
    python3 setup.py --auto  全默认（LM Studio localhost:1234）
"""

import os, shutil, subprocess, sys
from pathlib import Path

MEMENTO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = MEMENTO_ROOT / "Memory" / "config.yaml"
ENV_PATH = MEMENTO_ROOT / ".env"

PYTHON_MIN = (3, 10)

# ── 颜色 ─────────────────────────────────────────────
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def info(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def error(msg):
    print(f"  {RED}✗{RESET} {msg}")


def header(msg):
    print(f"\n{CYAN}{BOLD}{'=' * 50}{RESET}")
    print(f"{CYAN}{BOLD}  {msg}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * 50}{RESET}\n")


def ask(prompt: str, default: str = "") -> str:
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val or default
    return input(f"  {prompt}: ").strip()


def yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    val = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


# ── 阶段 1：环境检测 ──────────────────────────────────

def check_python():
    header("环境检测")
    v = sys.version_info
    if v >= PYTHON_MIN:
        info(f"Python {v.major}.{v.minor}.{v.micro} ✓")
    else:
        error(f"需要 Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+，当前 {v.major}.{v.minor}.{v.micro}")
        sys.exit(1)


def check_deps():
    missing = []
    for pkg in ("requests", "yaml", "numpy"):
        try:
            __import__(pkg)
            info(f"{pkg} ✓")
        except ImportError:
            missing.append(
                "pyyaml" if pkg == "yaml" else
                "numpy" if pkg == "numpy" else pkg
            )
    if missing:
        print()
        warn(f"缺少依赖: {', '.join(missing)}")
        if yn("自动安装? (pip install)"):
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *missing]
            )
            info("依赖安装完成")
        else:
            error("请手动安装: pip install " + " ".join(missing))
            sys.exit(1)


# ── 阶段 2：主模型 ───────────────────────────────────

def configure_main_model():
    header("主模型 配置")
    print("  主模型用于：提取 L0/L1/决策、查询重排序")
    print()
    if yn("使用本地 LM Studio?", True):
        base_url = ask("LM Studio 地址", "http://localhost:1234/v1")
        model = ask("模型名", "qwen3.6-35b-a3b-mlx")
        return {
            "provider": "lmstudio",
            "model": model,
            "base_url": base_url,
            "api_key": "",
        }
    else:
        print("\n  支持的 API 形式：")
        print("  • deepseek — api.deepseek.com")
        print("  • openai   — api.openai.com 或其他兼容服务")
        provider = ask("Provider", "deepseek")
        base_url = ask("API 地址", {
            "deepseek": "https://api.deepseek.com/v1",
            "openai": "https://api.openai.com/v1",
        }.get(provider, "https://api.deepseek.com/v1"))
        model = ask("模型名", {
            "deepseek": "deepseek-v4-flash",
            "openai": "gpt-4o-mini",
        }.get(provider, ""))
        api_key = ask("API Key", "sk-...")
        return {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        }


# ── 阶段 3：压缩模型 ─────────────────────────────────

def configure_compress_model():
    header("压缩模型 配置")
    print("  压缩模型用于：L2→L1→L0 压缩（成本较高建议用廉价 API）")
    print()
    if yn("使用专用压缩模型? (否则继承主模型)", False):
        return configure_main_model()
    return {
        "provider": "main",
        "model": "",
        "base_url": "",
        "api_key": "",
    }


# ── 阶段 4：向量模型 ────────────────────────────────

def configure_embed_model():
    header("向量模型 配置")
    print("  向量模型用于：语义搜索。建议用本地 LM Studio embedding。")
    print()
    if yn("使用本地 LM Studio embedding?", True):
        base_url = ask("LM Studio 地址", "http://localhost:1234/v1")
        model = ask("模型名", "qwen3-embedding-4b-mxfp8")
        dim = ask("向量维度", "2560")
        return {
            "provider": "lmstudio",
            "model": model,
            "base_url": base_url,
            "dimension": int(dim),
            "api_key": "",
        }
    else:
        print("\n  支持的 API 形式：")
        print("  • openai — text-embedding-3-small/large")
        provider = ask("Provider", "openai")
        model = ask("模型名", "text-embedding-3-small")
        dim = ask("向量维度", "1536")
        base_url = ask("API 地址", "https://api.openai.com/v1")
        api_key = ask("API Key", "sk-...")
        return {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "dimension": int(dim),
            "api_key": api_key,
        }


# ── 阶段 5：输出文件 ────────────────────────────────

def init_profile_vault():
    """创建默认 profile vault（profiles/default/memory-vault/）。"""
    vault = MEMENTO_ROOT / "profiles" / "default" / "memory-vault"
    if vault.exists():
        return  # 已存在
    (vault / "overviews" / "sessions").mkdir(parents=True, exist_ok=True)
    (vault / "storage" / "sessions").mkdir(parents=True, exist_ok=True)
    schema = MEMENTO_ROOT / "Memory" / "schema" / "v2.sql"
    if schema.exists():
        subprocess.run(["sqlite3", str(vault / "abstracts.db")], input=schema.read_text(), text=True, check=True, capture_output=True)
    info(f"profile vault 已创建: {vault}")

def write_config(main, compress, embed):
    init_profile_vault()
    config = {
        "models": {
            "main": main,
            "compress": compress,
            "embed": embed,
        },
        "paths": {
            "global_vault": "../Memory",
            "knowledge_base": "../Knowledge",
            "logs_dir": "logs",
            "profile_vault_base": f"{MEMENTO_ROOT}/profiles",
        },
        "retrieval": {
            "auto_inject_limit": 5,
        },
    }
    import yaml
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False))
    info(f"配置写入 {CONFIG_PATH}")


def write_env(api_key: str):
    if not api_key:
        return
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
    key_line = f"DEEPSEEK_API_KEY={api_key}"
    found = False
    for i, line in enumerate(lines):
        if line.startswith("DEEPSEEK_API_KEY="):
            lines[i] = key_line
            found = True
            break
    if not found:
        lines.append(key_line)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    info(f"API Key 写入 {ENV_PATH}")


# ── 阶段 6：连通性测试 ──────────────────────────────

def test_connections(main, embed):
    header("连通性测试")
    import requests

    all_ok = True
    print(f"  • 主模型 ({main['provider']} / {main['model']}) ... ", end="", flush=True)
    try:
        headers = {"Content-Type": "application/json"}
        if main.get("api_key"):
            headers["Authorization"] = f"Bearer {main['api_key']}"
        r = requests.post(
            f"{main['base_url']}/chat/completions",
            json={"model": main["model"], "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            print(f"{GREEN}OK{RESET}")
            info(f"    响应: {r.json()['choices'][0]['message']['content'][:50]}...")
        else:
            print(f"{RED}HTTP {r.status_code}: {r.text[:60]}{RESET}")
            all_ok = False
    except Exception as e:
        print(f"{RED}{e}{RESET}")
        all_ok = False

    # 测试向量模型
    print(f"  • 向量模型 ({embed['provider']} / {embed['model']}) ... ", end="", flush=True)
    try:
        headers = {"Content-Type": "application/json"}
        if embed.get("api_key"):
            headers["Authorization"] = f"Bearer {embed['api_key']}"
        r = requests.post(
            f"{embed['base_url']}/embeddings",
            json={"model": embed["model"], "input": "ping"},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            dim = len(r.json()["data"][0]["embedding"])
            print(f"{GREEN}OK (dim={dim}){RESET}")
        else:
            print(f"{RED}HTTP {r.status_code}: {r.text[:60]}{RESET}")
            all_ok = False
    except Exception as e:
        print(f"{RED}{e}{RESET}")
        all_ok = False

    # 测试数据库
    from Engine.src.client import resolve_path
    from Engine.src.retriever import get_db
    global_db = Path(resolve_path("../Memory")) / "abstracts.db"
    print(f"  • 数据库 ... ", end="", flush=True)
    if global_db.exists():
        print(f"{GREEN}已存在 ({global_db.stat().st_size / 1024:.0f} KB){RESET}")
    else:
        print(f"{YELLOW}尚未创建（首次注入时自动创建）{RESET}")

    return all_ok


# ── 主流程 ───────────────────────────────────────────

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    print(f"\n{BOLD}Memento 配置向导{RESET}")
    print(f"  根目录: {MEMENTO_ROOT}")
    print(f"  文档:   {MEMENTO_ROOT / 'USAGE.md'}")
    print()

    auto_mode = "--auto" in sys.argv

    check_python()
    check_deps()

    if auto_mode:
        print()
        info("自动模式: 使用全默认配置")
        main = {"provider": "lmstudio", "model": "qwen3.6-35b-a3b-mlx",
                "base_url": "http://localhost:1234/v1", "api_key": ""}
        compress = {"provider": "deepseek", "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com/v1", "api_key": ""}
        embed = {"provider": "lmstudio", "model": "qwen3-embedding-4b-mxfp8",
                 "base_url": "http://localhost:1234/v1", "dimension": 2560, "api_key": ""}
        write_config(main, compress, embed)
        write_env(compress.get("api_key"))
    else:
        main = configure_main_model()
        compress = configure_compress_model()
        embed = configure_embed_model()
        write_config(main, compress, embed)

        # 收集所有 API Key
        all_keys = set()
        for cfg in (main, compress, embed):
            if cfg.get("api_key") and cfg["api_key"] != "sk-...":
                all_keys.add(cfg["api_key"])
        for key in all_keys:
            write_env(key)

    print()
    if auto_mode or yn("运行连通性测试?", True):
        test_connections(main, embed)

    # ── 完成 ──
    header("配置完成")
    print(f"  配置文件: {CONFIG_PATH}")
    print(f"  快速启动:")
    print(f"    cd {MEMENTO_ROOT}/Engine")
    print(f"    python3 hooks/remember.py --help      # 注入记忆")
    print(f"    python3 src/retriever.py --help        # 检索")
    print(f"    python3 hooks/auto.py status           # 状态")
    print()
    warn("提示: DeepSeek API Key 也可以通过环境变量 DEEPSEEK_API_KEY 传入")
    print()


if __name__ == "__main__":
    main()
