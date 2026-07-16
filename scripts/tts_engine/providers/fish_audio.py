#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fish Audio provider。
接入细节（走 SOCKS5 代理、鉴权头、请求体结构、流式读、断流自愈）
全部内聚在这里，上层编排器完全不碰。

关键：Fish Audio 的模型名必须放在 HTTP 头 `model` 里（不是 body），
否则服务端会回退到付费默认模型并返回 402 Payment Required。
"""
import httpx
from .base import TTSProvider

API_URL = "https://api.fish.audio/v1/tts"


class FishAudioProvider(TTSProvider):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.api_key = cfg["api_key"]
        self.voice = cfg.get("voice_id") or cfg.get("voice", "")
        self.model = cfg.get("model", "s2.1-pro-free")
        self.bitrate = cfg.get("mp3_bitrate", 128)
        self._client = None

    def _client_get(self) -> httpx.Client:
        if self._client is None:
            proxy = self.cfg.get("socks")
            kwargs = dict(timeout=httpx.Timeout(connect=10.0, read=120.0,
                                                write=30.0, pool=30.0))
            if proxy:
                kwargs["proxy"] = f"socks5://{proxy}"
            self._client = httpx.Client(**kwargs)
        return self._client

    def synth_chunk(self, text: str) -> bytes:
        payload = {
            "text": text,
            "reference_id": self.voice,
            "format": "mp3",
            "mp3_bitrate": self.bitrate,
            "latency": "normal",
            "chunk_length": 300,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "model": self.model,
            "Content-Type": "application/json",
        }
        last_err = None
        for _ in range(3):
            try:
                c = self._client_get()
                with c.stream("POST", API_URL, json=payload, headers=headers) as r:
                    r.raise_for_status()
                    buf = b""
                    for piece in r.iter_bytes():
                        buf += piece
                    return buf
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        raise RuntimeError(f"FishAudio synth failed: {last_err}")

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
