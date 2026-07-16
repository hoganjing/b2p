#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 注册表：名字 -> 类。新增 API 只需在此登记 + 写子类。"""
from .base import TTSProvider
from .fish_audio import FishAudioProvider
from .edge_tts import EdgeTTSProvider
from .mimo import MimoProvider
from .mimo_voicedesign import MimoVoiceDesignProvider
from .mimo_voiceclone import MimoVoiceCloneProvider

REGISTRY = {
    "fish_audio": FishAudioProvider,
    "edge_tts": EdgeTTSProvider,
    "mimo": MimoProvider,
    "mimo_voicedesign": MimoVoiceDesignProvider,
    "mimo_voiceclone": MimoVoiceCloneProvider,
}


def get_provider(name: str, cfg: dict) -> TTSProvider:
    if name not in REGISTRY:
        raise KeyError(
            f"未知 provider: {name}；可选：{', '.join(REGISTRY.keys())}"
        )
    return REGISTRY[name](cfg)
