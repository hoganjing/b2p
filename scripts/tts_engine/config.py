#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加载 apis.yaml，解析出要用的 provider 名字 + 该 profile 配置。"""
import os
import yaml


def load_apis(path: str = "apis.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(path: str, api_name: str | None) -> tuple[str, str, dict]:
    """
    返回 (provider_type, profile_name, profile_cfg)。
    api_name 优先级：CLI 指定 > 环境变量 TTS_PROVIDER > apis.yaml 的 default。
    """
    cfg = load_apis(path)
    profiles = cfg.get("apis", {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("apis.yaml 中必须包含非空的 apis: 字段")

    chosen = api_name or os.environ.get("TTS_PROVIDER") or cfg.get("default")
    if not chosen:
        # 取第一个作为兜底
        chosen = next(iter(profiles))
    if chosen not in profiles:
        raise KeyError(f"profile '{chosen}' 不在 apis.yaml 的 apis 中")

    profile = profiles[chosen]
    ptype = profile.get("type")
    if not ptype:
        raise ValueError(f"profile '{chosen}' 缺少 type 字段")
    return ptype, chosen, profile
