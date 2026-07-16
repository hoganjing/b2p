#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edge-TTS provider（微软免费接口，无需 key，仅 voice 名）。
异步接口在同步 synth_chunk 内通过 asyncio.run 调用。
"""
import asyncio
import io

import edge_tts
from .base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.voice = cfg.get("voice", "zh-CN-XiaoxiaoNeural")
        self.rate = cfg.get("rate", "+0%")
        self.volume = cfg.get("volume", "+0%")

    def synth_chunk(self, text: str) -> bytes:
        return asyncio.run(self._run(text))

    async def _run(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(
            text, self.voice, rate=self.rate, volume=self.volume
        )
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()
