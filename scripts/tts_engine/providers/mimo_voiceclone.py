#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 mimo TTS —— 音色复刻（voice clone）模式：mimo-v2.5-tts-voiceclone。

与语音设计（voicedesign.py）的区别（依据官方文档
https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5 ）：

- model 固定为 "mimo-v2.5-tts-voiceclone"。
- 不需要自然语言声线描述；音色完全由一段「参考音频」钉死：
  参考音频放 audio.voice，格式为带 MIME 前缀的 Base64：
      "data:audio/wav;base64,{BASE64_AUDIO}"   或  "data:audio/mpeg;base64,{...}"
  仅支持 mp3 / wav 作为参考（不支持 pcm16），Base64 体积须 <10MB。
- 合成文本放 messages[assistant].content（user 可留空或放风格指令，不进语音）。
- 输出格式由 audio.format 控制，仅 wav / pcm16；**请求 mp3 会被忽略、照样回 wav**。
  为统一交付 mp3，provider 用本地 ffmpeg（imageio-ffmpeg 自带二进制）转码。
- 音色复刻靠锚点钉身份，temperature / top_p 仅影响韵律的采样方差（不影响说话人身份）。
  本实现透传这两个字段，并默认设到文档范围下限（temperature=0、top_p=0.01），
  让每次生成音色/韵律变化最小（即便模型对非确定性端点可能忽略，传最小值是无害的）。
- 典型用法：先用 voice_design 铸一段「锚点音频」落下声线，之后全书所有切块
  都走 voiceclone、共用同一锚点 → 跨章节音色（说话人身份）一致。

接入细节内聚在此；复用父类 MimoProvider 的端点 / 鉴权 / 响应解析。
"""
import os
import base64
import subprocess

from .mimo import MimoProvider


class MimoVoiceCloneProvider(MimoProvider):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        # 音色复刻模型固定
        self.model = cfg.get("model", "mimo-v2.5-tts-voiceclone")
        # 参考音频：优先 anchor（文件路径），其次 anchor_b64（裸 base64 字符串）
        self.anchor_path = cfg.get("anchor")
        self.anchor_b64 = cfg.get("anchor_b64")
        if self.anchor_path:
            self.anchor_path = os.path.expanduser(self.anchor_path)
        self.anchor_mime = cfg.get("anchor_mime") or self._guess_mime(self.anchor_path)
        # API 只出 wav/pcm16；本 provider 默认转码为 mp3 交付（与全书其余章节统一）。
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
        self._ffmpeg_exe = None
        self._voice_b64 = None  # 缓存带前缀的 base64
        # 音色复刻靠锚点钉身份；temperature/top_p 仅影响韵律采样方差。
        # 默认设到文档范围下限，让每次生成音色/韵律变化最小。
        self.temperature = cfg.get("temperature", 0.0)
        self.top_p = cfg.get("top_p", 0.01)

    @staticmethod
    def _guess_mime(path):
        if not path:
            return "audio/wav"
        ext = os.path.splitext(path)[1].lower()
        return "audio/mpeg" if ext in (".mp3", ".mpeg") else "audio/wav"

    def _load_voice(self) -> str:
        """读取参考音频，返回带 MIME 前缀的 Base64（data:{mime};base64,{b64}）。"""
        if self._voice_b64:
            return self._voice_b64
        if self.anchor_b64:
            raw = self.anchor_b64
        elif self.anchor_path and os.path.exists(self.anchor_path):
            raw = base64.b64encode(open(self.anchor_path, "rb").read()).decode("ascii")
        else:
            raise ValueError(
                "voiceclone 必须提供 anchor（参考音频文件路径）或 anchor_b64（裸 base64）"
            )
        self._voice_b64 = f"data:{self.anchor_mime};base64,{raw}"
        return self._voice_b64

    def _build_payload(self, text: str) -> dict:
        # API 只接受 wav / pcm16；mp3 由本地转码得到，所以真实请求固定走 wav。
        api_fmt = "pcm16" if self.fmt == "pcm16" else "wav"
        audio = {
            "format": api_fmt,
            "voice": self._load_voice(),   # 带前缀的 base64 参考音频
        }
        return {
            "model": self.model,
            "messages": [
                {"role": "user", "content": ""},          # 可选风格指令，留空
                {"role": "assistant", "content": text},   # 合成文本必须在此
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
            return (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[:3] == b"ID3"
        if self.fmt == "wav":
            return b[:4] == b"RIFF"
        return True  # pcm16 裸流无头
