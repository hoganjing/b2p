#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 mimo TTS provider（mimo-v2.5-tts）。
依据官方文档 https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5 :

关键约束（与最初实现不同的修正点）：
- Endpoint: https://api.xiaomimimo.com/v1/chat/completions  （不是 mimo.xiaomi.com）
- 鉴权头: `api-key: <KEY>`  （不是 Authorization: Bearer）
- 合成文本必须放在 messages 的 role=assistant 的 content 里（user 仅放风格指令，不出现在语音中）
- audio.format 支持 mp3 / wav / pcm16（官方当前 API 文档已列出 mp3，故默认请求 mp3、交付 .mp3）
- 响应音频 base64 在 choices[0].message.audio.data
- 中文预置音色：冰糖 / 茉莉（女声）、苏打 / 白桦（男声）
所有接入细节内聚在此。

重试策略（依据官方 rate-limit / error-codes 文档）：
- 429/500/502/503/504 及网络异常 → 指数退避重试（读 Retry-After 头，base=2s，封顶 30s，最多 4 次）。
- 4xx（400/401/402/403/404/421 等）→ 立即抛 TTSFatalError，不重试（参数/认证/内容问题重试无效）。
- 退避耗尽仍失败 → 抛 TTSRetryableError，由上层决定放弃该块。
"""
import base64
import time
import threading
import httpx
from .base import TTSProvider, TTSFatalError, TTSRetryableError


class MimoProvider(TTSProvider):
    # 可退避重试的 HTTP 状态码（官方错误码页：429 请求超限 / 500 服务器失败 /
    # 503 负载过高；502/504 网关类亦按可重试处理）。
    _RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
    _BACKOFF_BASE = 2.0    # 初始退避秒数
    _BACKOFF_MAX = 60.0    # 单次退避上限（秒）
    _MAX_RETRIES = 6       # 可重试错误的最大重试次数（不含首次请求）
    # 说明：限流(429)是按"请求速率/令牌桶"计的，桶被抽干后需靠退避等待回填。
    # 实测重限流下 17 字请求也要连吃 4 次 429 才放行，故留足重试次数与退避上限，
    # 让单块能"熬过"限流而非轻易 FATAL。退避期间不发请求，正好给桶回填时间。

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.endpoint = cfg.get(
            "endpoint", "https://api.xiaomimimo.com/v1/chat/completions"
        )
        self.model = cfg.get("model", "mimo-v2.5-tts")
        self.voice = cfg.get("voice", "冰糖")   # 中文女声，中国集群默认
        self.api_key = cfg.get("api_key", "")
        # 默认 mp3（官方 API 文档已支持），也可在 apis.yaml 写 format: wav
        self.fmt = cfg.get("format", "mp3")
        self.ext = ".mp3" if self.fmt == "mp3" else ".wav"
        # 采样超参（官方"模型超参"页确认 TTS 模型支持 temperature 默认0.6、top_p 默认0.95）。
        # 实测即使 temp=0 也无法保证确定性输出；这里提供 config 入口，默认调低以减少跨切块韵律方差。
        self.temperature = cfg.get("temperature", 0.6)
        self.top_p = cfg.get("top_p", 0.95)
        self._client = None

    def _client_get(self):
        # 显式超时：connect 防连接建立阶段僵死；read 防半开连接无限等；
        # write/pool 防上传锚点/建连池卡死。任一超时会抛出 httpx.TimeoutException，
        # 被 _fetch 的 except 捕获并按可重试处理（指数退避后重试，耗尽则 [FATAL] 跳过该块）。
        _TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=60.0)
        if self._client is None:
            proxy = self.cfg.get("socks")
            if proxy:
                self._client = httpx.Client(
                    proxy=f"socks5://{proxy}",
                    timeout=_TIMEOUT,
                )
            else:
                self._client = httpx.Client(timeout=_TIMEOUT)
        return self._client

    def _build_payload(self, text: str) -> dict:
        """构造请求体。子类（如 voice design 模式）可覆写以改变消息结构。"""
        return {
            "model": self.model,
            "messages": [
                {"role": "user", "content": ""},      # 可选风格指令，留空
                {"role": "assistant", "content": text},  # 合成文本必须在此
            ],
            "audio": {"voice": self.voice, "format": self.fmt},
            "temperature": self.temperature,
            "top_p": self.top_p,
        }

    @staticmethod
    def _sleep_backoff(resp, attempt: int):
        """指数退避；若响应带 Retry-After 头则优先采用其秒数。"""
        if resp is not None:
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                delay = min(float(ra), MimoProvider._BACKOFF_MAX)
                time.sleep(delay)
                return
        delay = min(MimoProvider._BACKOFF_BASE * (2 ** attempt), MimoProvider._BACKOFF_MAX)
        time.sleep(delay)

    def _post_with_heartbeat(self, payload, headers):
        """发起阻塞 POST，同时每 15s 打印一行心跳。
        限流后 mimo 服务端会排队慢响应（可长达数分钟），这里让等待期间也有回响，
        既能确认进程活着，也能看清是在排队等待、还是真卡死。"""
        result = {}
        done = threading.Event()

        def _worker():
            try:
                result["resp"] = self._client_get().post(
                    self.endpoint, json=payload, headers=headers
                )
            except Exception as e:  # noqa: BLE001 交回主线程按可重试处理
                result["exc"] = e
            finally:
                done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t0 = time.time()
        t.start()
        # 每 15s 心跳一次，直到请求返回或读超时(600s)自然结束
        while not done.wait(timeout=15):
            waited = int(time.time() - t0)
            print(f"      ⏳ 已等待 {waited}s（mimo 限流排队中，读超时上限 600s）...",
                  flush=True)
        if "exc" in result:
            raise result["exc"]
        return result["resp"]

    def _fetch(self, text: str) -> bytes:
        payload = self._build_payload(text)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        last_err = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                r = self._post_with_heartbeat(payload, headers)
                if r.status_code in self._RETRYABLE_STATUS:
                    # 限流 / 服务器抖动：先吭一声，再退避后重试
                    last_err = f"HTTP {r.status_code}"
                    ra = r.headers.get("Retry-After")
                    ra_hint = f"，服务端要求等 {ra}s" if ra else ""
                    if attempt < self._MAX_RETRIES:
                        print(f"      ⚠ 收到 {last_err}（限流/服务端抖动{ra_hint}），"
                              f"第 {attempt + 1}/{self._MAX_RETRIES} 次退避重试...",
                              flush=True)
                    else:
                        print(f"      ✗ 收到 {last_err}，已是最后一次尝试，即将放弃该块",
                              flush=True)
                    self._sleep_backoff(r, attempt)
                    continue
                if 400 <= r.status_code < 500:
                    # 4xx 致命错误：参数/认证/余额/内容审核，重试无意义
                    body = r.text[:300]
                    raise TTSFatalError(f"Mimo {r.status_code} fatal: {body}")
                r.raise_for_status()  # 其余非预期状态（如 3xx）也抛
                data = r.json()
                b64 = data["choices"][0]["message"]["audio"]["data"]
                return base64.b64decode(b64)
            except TTSFatalError:
                raise
            except Exception as e:  # noqa: BLE001  网络异常/超时/JSON 解析等，按可重试处理
                last_err = e
                emsg = f"{type(e).__name__}: {str(e)[:120]}"
                if attempt < self._MAX_RETRIES:
                    print(f"      ⚠ 请求异常（{emsg}），"
                          f"第 {attempt + 1}/{self._MAX_RETRIES} 次退避重试...",
                          flush=True)
                    self._sleep_backoff(None, attempt)
                else:
                    print(f"      ✗ 请求异常（{emsg}），已是最后一次尝试，即将放弃该块",
                          flush=True)
                continue
        # 退避重试耗尽（持续 5xx / 网络异常）
        raise TTSRetryableError(
            f"Mimo synth exhausted after {self._MAX_RETRIES} retries: {last_err}"
        )

    def synth_chunk(self, text: str) -> bytes:
        return self._fetch(text)

    def is_valid(self, b: bytes) -> bool:
        if not b:
            return False
        if self.fmt == "mp3":
            # mp3 帧头：0xFF 且 (b[1] & 0xE0)==0xE0，或 ID3 标签
            return (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[:3] == b"ID3"
        # wav 文件以 RIFF 头起始
        return b[:4] == b"RIFF"

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
