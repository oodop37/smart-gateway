"""
router.py - 核心路由引擎
综合分排序 → 依次尝试 → 失败熔断 → 流式容灾切换
"""

import asyncio
import json
import time
import logging
from typing import AsyncGenerator, Optional

import httpx

# 在模块级别导入 prometheus 指标（避免循环导入）
try:
    from app import MODEL_ROUTING_COUNT
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

logger = logging.getLogger("smart-gateway.router")


class SmartRouter:
    """
    智能路由引擎

    路由流程：
    1. 请求进入 → 查询可用模型列表（按综合分从高到低排序）
    2. 跳过熔断中的模型
    3. 依次尝试发送请求
       - 成功 → 记录成功，加稳定分，返回结果
       - 失败 → 记录失败，扣稳定分，尝试下一个
       - 流式中断 → 自动切下一个模型继续输出（用户无感）
    4. 全部失败 → 返回 503
    """

    def __init__(self, db, config: dict):
        self.db = db
        self.config = config
        self.alpha = config.get("scoring", {}).get("ability_weight", 0.4)
        self.beta = config.get("scoring", {}).get("stability_weight", 0.6)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0),
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    async def route_chat_completion(
        self,
        model_name: str,
        messages: list,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """
        路由聊天请求

        Args:
            model_name: 模型名 / 路由组名 / "auto"
            messages: 消息列表
            stream: 是否流式
            **kwargs: 其他参数（temperature, max_tokens 等）

        Returns:
            OpenAI 兼容的响应
        """
        candidates = self.db.get_available_models(model_name)

        if not candidates:
            return {
                "error": {
                    "message": f"No available models for '{model_name}'. "
                               f"All models are either disabled or circuit-broken.",
                    "type": "router_error",
                    "code": 503,
                }
            }

        logger.info(
            "Routing '%s': %d candidates available",
            model_name, len(candidates)
        )

        if stream:
            return self._route_stream(candidates, messages, **kwargs)
        else:
            return await self._route_non_stream(candidates, messages, **kwargs)

    async def _route_non_stream(
        self,
        candidates: list,
        messages: list,
        **kwargs,
    ) -> dict:
        """非流式：按顺序尝试，失败切下一个"""
        last_error = None

        for c in candidates:
            try:
                provider_url = c["base_url"].rstrip("/")
                api_key = c["api_key"]
                model_name = c["model_name"]

                payload = {
                    "model": model_name,
                    "messages": messages,
                    "stream": False,
                    **{k: v for k, v in kwargs.items() if v is not None},
                }

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }

                t0 = time.time()
                resp = await self.client.post(
                    f"{provider_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                latency = int((time.time() - t0) * 1000)

                if resp.is_success:
                    data = resp.json()
                    # 注入 🤖 Provider · model 前缀
                    prefix = f"🤖 {c['provider_name']} · {c['model_name']}\n\n"
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content is not None:
                        data["choices"][0]["message"]["content"] = prefix + content
                    tokens_in = data.get("usage", {}).get("prompt_tokens", 0)
                    tokens_out = data.get("usage", {}).get("completion_tokens", 0)
                    self.db.on_request_success(
                        c["id"], latency, tokens_in, tokens_out
                    )
                    if HAS_PROMETHEUS:
                        MODEL_ROUTING_COUNT.labels(
                            provider=c["provider_name"],
                            model=c["model_name"],
                            status="success",
                        ).inc()
                    logger.info(
                        "✅ %s/%s succeeded (%.1fs)",
                        c["provider_name"], c["model_name"], latency / 1000
                    )
                    return data
                else:
                    error_text = await resp.aread()
                    error_msg = error_text.decode()[:200]
                    self.db.on_request_failure(c["id"], error_msg, latency)
                    if HAS_PROMETHEUS:
                        MODEL_ROUTING_COUNT.labels(
                            provider=c["provider_name"],
                            model=c["model_name"],
                            status="error",
                        ).inc()
                    last_error = f"{c['provider_name']}/{c['model_name']}: {resp.status_code} {error_msg}"
                    logger.warning("❌ %s failed: %s", c["model_name"], last_error)

            except Exception as e:
                latency = 0
                self.db.on_request_failure(c["id"], str(e)[:200], latency)
                if HAS_PROMETHEUS:
                    MODEL_ROUTING_COUNT.labels(
                        provider=c["provider_name"],
                        model=c["model_name"],
                        status="exception",
                    ).inc()
                last_error = f"{c['provider_name']}/{c['model_name']}: {str(e)}"
                logger.warning("❌ %s error: %s", c["model_name"], last_error)

        # 全部失败
        return {
            "error": {
                "message": f"All models failed. Last error: {last_error}",
                "type": "router_error",
                "code": 503,
            }
        }

    async def _route_stream(
        self,
        candidates: list,
        messages: list,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """流式：在流中断时自动切换下一个模型（尝试从一个模型转另一个）"""
        last_error = None

        for c in candidates:
            try:
                provider_url = c["base_url"].rstrip("/")
                api_key = c["api_key"]
                model_name = c["model_name"]

                payload = {
                    "model": model_name,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    **{k: v for k, v in kwargs.items() if v is not None},
                }

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }

                t0 = time.time()
                async with self.client.stream(
                    "POST",
                    f"{provider_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    latency = int((time.time() - t0) * 1000)

                    if not resp.is_success:
                        error_text = await resp.aread()
                        error_msg = error_text.decode()[:200]
                        self.db.on_request_failure(c["id"], error_msg, latency)
                        last_error = f"{c['provider_name']}/{c['model_name']}: {resp.status_code}"
                        logger.warning(
                            "❌ %s stream failed: %s", c["model_name"], last_error
                        )
                        continue

                    # 流式成功，记录
                    self.db.on_request_success(c["id"], latency, 0, 0)
                    logger.info(
                        "✅ %s/%s stream started (%.1fs)",
                        c["provider_name"], c["model_name"], latency / 1000
                    )

                    # 开始发出 SSE 格式
                    yield f'data: {json.dumps({"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant","content":""},"index":0,"finish_reason":None}]})}\n\n'

                    # 注入 🤖 Provider · model 前缀（第一个文本 delta）
                    prefix = f"🤖 {c['provider_name']} · {c['model_name']}\n\n"
                    yield f'data: {json.dumps({"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":prefix},"finish_reason":None}]})}\n\n'

                    # 逐行读取流
                    content_so_far = ""
                    tokens_in = 0
                    tokens_out = 0

                    try:
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data = json.loads(data_str)
                                    # 提取 token 用量
                                    usage = data.get("usage", {})
                                    if usage:
                                        tokens_in = usage.get("prompt_tokens", 0)
                                        tokens_out = usage.get("completion_tokens", 0)
                                    yield line + "\n\n"
                                except json.JSONDecodeError:
                                    yield line + "\n\n"
                    except Exception as e:
                        # 流中断！记录失败，但我们已经出了部分内容
                        logger.warning(
                            "⚠️ %s stream interrupted: %s", c["model_name"], str(e)
                        )
                        # 更新 token 用量
                        self.db.on_request_failure(c["id"], str(e)[:200], 0)
                        # 尝试下一个模型继续
                        nl = "\n\n"
                        yield f'data: {json.dumps({"object":"chat.completion.chunk","choices":[{"delta":{"content":f"{nl}[⚠️ 上游中断，切换到下一个模型继续...]{nl}"},"index":0,"finish_reason":None}]})}\n\n'
                        continue

                    # 正常结束
                    yield 'data: [DONE]\n\n'
                    # 更新最终 token 用量
                    self.db.on_request_success(c["id"], 0, tokens_in, tokens_out)
                    return

            except Exception as e:
                latency = 0
                self.db.on_request_failure(c["id"], str(e)[:200], latency)
                last_error = f"{c['provider_name']}/{c['model_name']}: {str(e)}"
                logger.warning("❌ %s error: %s", c["model_name"], last_error)

        # 全部失败
        error_msg = last_error or "All models failed"
        yield f'data: {json.dumps({"error":{"message":error_msg,"type":"router_error","code":503}})}\n\n'
        yield 'data: [DONE]\n\n'

    # list_models 已统一到 app._build_model_list()，此处不再保留