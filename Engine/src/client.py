"""Memento — 通用 LLM/Embedding 客户端。

统一调用入口，支持 provider 继承和 fallback。
所有路径相对于 Engine/ 目录自动解析。
"""

import os
from pathlib import Path

import requests
import yaml


def _get_engine_dir() -> Path:
    """返回 Engine/ 目录的绝对路径。"""
    return Path(__file__).resolve().parent.parent


def _load_env() -> dict:
    """从 ~/.hermes/.env 或 Memento/.env 加载环境变量（向后兼容）。"""
    env = {}
    candidates = [
        Path.home() / ".hermes" / ".env",
        _get_engine_dir().parent / ".env",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    return env


def _load_config() -> dict:
    """加载 Memory/config.yaml。"""
    cfg_path = _get_engine_dir().parent / "Memory" / "config.yaml"
    return yaml.safe_load(cfg_path.read_text())


def resolve_path(relative: str) -> str:
    """将相对于 Engine/ 的路径解析为绝对路径。"""
    if not relative:
        return ""
    p = _get_engine_dir() / relative
    return str(p.resolve())


def resolve_provider(config_key: str = "main") -> dict:
    """解析 provider 配置，支持继承 main 的默认值。

    compress 留空则继承 main 的 provider 和 model。
    """
    cfg = _load_config()
    models = cfg.get("models", {})
    key_config = models.get(config_key, {})

    # 如果配置为空，继承 main
    if not key_config or key_config.get("provider") in (None, "main"):
        main = models.get("main", {})
        base_url = key_config.get("base_url") or main.get("base_url", "")
        model = key_config.get("model") or main.get("model", "")
        api_key = key_config.get("api_key") or main.get("api_key", "")
        env = _load_env()
        env_key = env.get("DEEPSEEK_API_KEY", "")
        return {
            "provider": key_config.get("provider") or main.get("provider", "lmstudio"),
            "model": model,
            "base_url": base_url,
            "api_key": api_key or os.environ.get("DEEPSEEK_API_KEY", "") or env_key,
        }

    env = _load_env()
    env_key = env.get("DEEPSEEK_API_KEY", "")
    return {
        "provider": key_config.get("provider", "lmstudio"),
        "model": key_config.get("model", ""),
        "base_url": key_config.get("base_url", ""),
        "api_key": key_config.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "") or env_key,
    }


def call_llm(messages: list[dict], config_key: str = "main", **kwargs) -> str:
    """统一 LLM 调用。支持 lmstudio / deepseek / openai。

    Args:
        messages: [{"role": "user", "content": "..."}]
        config_key:  models 配置段键名（main / compress）
        **kwargs:  可传入 model/stream/temperature 覆盖配置

    Returns:
        LLM 返回的文本内容。
    """
    cfg = resolve_provider(config_key)
    model = kwargs.pop("model", cfg["model"])
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]
    timeout = kwargs.pop("timeout", 300)

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        **kwargs,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"LLM 调用失败 [{resp.status_code}] provider={cfg['provider']} "
            f"model={model}: {resp.text[:200]}"
        )
    return resp.json()["choices"][0]["message"]["content"].strip()


def call_embedding(text: str, config_key: str = "embed") -> list[float]:
    """统一 Embedding 调用。

    Args:
        text: 要向量化的文本
        config_key: models 配置段键名（默认 embed）

    Returns:
        向量列表（float）。
    """
    cfg = resolve_provider(config_key)
    model = cfg.get("model", "text-embedding-nomic-embed-text-v1.5")
    base_url = cfg.get("base_url", "http://localhost:1234/v1")
    api_key = cfg.get("api_key", "")
    timeout = 30

    payload = {"model": model, "input": text}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(f"{base_url}/embeddings", json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Embedding 调用失败 [{resp.status_code}] provider={cfg.get('provider')} "
            f"model={model}: {resp.text[:200]}"
        )
    return resp.json()["data"][0]["embedding"]
