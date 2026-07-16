#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTSProvider 抽象基类。

设计要点（也是这次重构的核心）：
- 每个具体 API 的「怎么调」——鉴权头格式、请求体结构、流式还是非流式、
  走不走代理、限流如何处理——全部内聚在各自子类里。
- 上层编排器（tts_gen.py）只认这一个接口：synth_chunk(text) -> bytes。
  它不关心具体是 Fish / Edge / Mimo 还是别的什么。
- 这样新增一个「使用方式不同」的 API，只需写一个子类 + 在 apis.yaml
  加一条 profile，编排器一行都不用改。
"""
import abc


class TTSFatalError(Exception):
    """不可重试的错误（如 4xx 参数/认证/内容审核错误）。上层不应重试或对半切自愈。"""


class TTSRetryableError(Exception):
    """可重试但已耗尽退避次数的错误（如持续 5xx / 网络异常）。"""


class TTSProvider(abc.ABC):
    def __init__(self, cfg: dict):
        # cfg 来自 apis.yaml 里该 profile 的全部字段
        self.cfg = cfg or {}
        # 该 provider 产出音频的扩展名（默认 .mp3；mimo 按 format 为 .mp3/.wav）
        self.ext = ".mp3"

    @abc.abstractmethod
    def synth_chunk(self, text: str) -> bytes:
        """把一小段文本合成成音频字节流（格式由子类决定，通常 mp3）。"""
        raise NotImplementedError

    def is_valid(self, b: bytes) -> bool:
        """校验合成的音频字节流头部是否合法（默认按 mp3 帧头判断）。"""
        if not b:
            return False
        return (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[:3] == b"ID3"

    # 可选：子类如需在结束时释放资源（如关连接）可重写
    def close(self):
        pass
