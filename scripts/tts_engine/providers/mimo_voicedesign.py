#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 mimo TTS —— 语音设计（voice design）模式：mimo-v2.5-tts-voicedesign。

与预设音色模式（mimo.py）的核心区别（依据官方文档
https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5 ）：

- model 固定为 "mimo-v2.5-tts-voicedesign"。
- 没有固定音色名，也不使用 audio.voice 字段；音色由「自然语言描述」决定：
  messages[user].content 必填，写音色设计描述（性别/年龄/音色/语气/语速等），
  且 user 内容**不会**出现在合成语音里。
- 真正的合成文本仍放 messages[assistant].content。
- **注意**：该模型 API 本身只输出 wav / pcm16（请求 mp3 也会被忽略、照样回 wav）。
  为满足「统一交付 mp3」的需求，本 provider 在拿到 wav 后用本地 ffmpeg
  （imageio-ffmpeg 自带二进制，无需系统安装）转码为 mp3；默认 fmt=mp3。
- 可选 audio.optimize_text_preview（仅 voice design 支持）：true 时模型可智能润色文本；
  本实现默认 false，保证脚本逐字照读。
- 响应音频 base64 仍在 choices[0].message.audio.data。
- 鉴权头 api-key、endpoint 与预设模式完全一致。

所有接入细节内聚在此；复用父类 MimoProvider 的端点/鉴权/响应解析。
"""
import subprocess
from .mimo import MimoProvider

# 默认音色设计描述：一套适合「知识讲解类播客」的明亮、有底气、专业不轻浮男声。
DEFAULT_VOICE_DESIGN = (
    "一位三十岁上下的男性，声音明亮清晰、干净利落、底气充足又有活力，"
    "像在讲一档高质量知识节目的主讲人；语速轻快流畅但不赶，"
    "语气热情投入、有感染力，专业而不轻浮。"
)


class MimoVoiceDesignProvider(MimoProvider):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        # 语音设计模型固定
        self.model = cfg.get("model", "mimo-v2.5-tts-voicedesign")
        # 音色设计描述文本（必填），放 user 消息；缺省给一套播客男声描述
        self.voice_design = cfg.get("voice_design", DEFAULT_VOICE_DESIGN)
        # API 只出 wav/pcm16；本 provider 默认转码为 mp3 交付（与全书其余章节统一）。
        # 若显式写 format: wav 则直接交付 wav；pcm16 交付裸流 .pcm。
        self.fmt = cfg.get("format", "mp3")
        if self.fmt == "mp3":
            self.ext = ".mp3"
        elif self.fmt == "wav":
            self.ext = ".wav"
        elif self.fmt == "pcm16":
            self.ext = ".pcm"
        else:
            self.fmt = "mp3"
            self.ext = ".mp3"
        # 是否让模型智能润色文本（默认关，保证逐字照读）
        self.optimize = bool(cfg.get("optimize", False))
        self._ffmpeg_exe = None

    def _build_payload(self, text: str) -> dict:
        # API 只接受 wav / pcm16；mp3 由本地转码得到，所以真实请求固定走 wav。
        api_fmt = "pcm16" if self.fmt == "pcm16" else "wav"
        audio = {"format": api_fmt}
        if self.optimize:
            audio["optimize_text_preview"] = True
        return {
            "model": self.model,
            "messages": [
                # user 必填：音色设计描述（不会进入语音）
                {"role": "user", "content": self.voice_design},
                # assistant：真正的合成文本
                {"role": "assistant", "content": text},
            ],
            "audio": audio,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }

    def _to_mp3(self, wav_bytes: bytes) -> bytes:
        """用本地 ffmpeg（imageio-ffmpeg 自带）把 wav 字节流转码为 mp3 字节流。"""
        if self._ffmpeg_exe is None:
            from imageio_ffmpeg import get_ffmpeg_exe
            self._ffmpeg_exe = get_ffmpeg_exe()
        proc = subprocess.run(
            [self._ffmpeg_exe, "-i", "pipe:0", "-f", "mp3",
             "-b:a", "128k", "-ar", "44100", "pipe:1"],
            input=wav_bytes, capture_output=True, check=True,
        )
        return proc.stdout

    def synth_chunk(self, text: str) -> bytes:
        wav = self._fetch(text)  # API 实际返回 wav（或 pcm16 裸流）
        if self.fmt == "mp3":
            return self._to_mp3(wav)
        return wav

    def is_valid(self, b: bytes) -> bool:
        if not b:
            return False
        if self.fmt == "mp3":
            # mp3 帧头：0xFF 且 (b[1] & 0xE0)==0xE0，或 ID3 标签
            return (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[:3] == b"ID3"
        if self.fmt == "wav":
            # wav 文件以 RIFF 头起始
            return b[:4] == b"RIFF"
        # pcm16 裸流无头，只要有内容即可
        return True
