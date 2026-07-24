"""
compressor.py - 上下文压缩
集成 RTK 或内置轻量压缩，省 token
"""

import re
import hashlib
import logging
import json
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("smart-gateway.compressor")


class ContextCompressor:
    """
    上下文压缩器
    3 层压缩：
    1. Prompt 精简（去除冗余空格/换行）
    2. 历史对话压缩（保留语义，压缩旧消息）
    3. 语义缓存（相同 prompt 直接返回）
    """

    def __init__(self, db, config: dict):
        self.db = db
        self.config = config.get("compression", {})
        self.enabled = self.config.get("enabled", False)
        self.mode = self.config.get("mode", "builtin")
        self.max_context_tokens = self.config.get("max_context_tokens", 4096)
        self.cache_ttl_minutes = self.config.get("cache_ttl_minutes", 30)
        self._cache = {}  # 简单内存缓存 {hash: {"result":..., "expires":...}}

    def compress_messages(self, messages: list) -> list:
        """
        压缩消息列表
        - 去除冗余
        - 如果消息太多，压缩历史
        """
        if not messages:
            return messages

        # Layer 1: 基础精简
        compressed = [self._trim_message(m) for m in messages]

        # Layer 2: 如果消息太多，压缩历史对话
        # 估算 token 数（粗略：1 中文字 ≈ 1.5 token, 1 英文字 ≈ 0.25 token）
        total_text = " ".join(m.get("content", "") for m in compressed)
        estimated_tokens = len(re.findall(r"[\u4e00-\u9fff]", total_text)) * 1.5 + \
                           len(re.findall(r"[a-zA-Z]", total_text)) * 0.25

        if estimated_tokens > self.max_context_tokens:
            compressed = self._compress_history(compressed)
            logger.info(
                "压缩历史对话: %d → %d tokens (估)", 
                int(estimated_tokens), 
                int(self._estimate_tokens(compressed))
            )

        return compressed

    def _trim_message(self, msg: dict) -> dict:
        """精简单条消息"""
        content = msg.get("content", "")

        if isinstance(content, str):
            # 去除多余空行（保留单个换行）
            content = re.sub(r"\n{3,}", "\n\n", content)
            # 去除行首行尾空白
            content = content.strip()
            # 去除行内连续空格（但保留行首缩进）
            content = re.sub(r"(\S) {2,}", r"\1 ", content)

        return {**msg, "content": content}

    def _compress_history(self, messages: list) -> list:
        """
        压缩历史对话
        策略：保留 system + 最近 N 条，中间的压缩成摘要
        """
        if len(messages) <= 6:
            return messages

        # 分离 system 消息和对话消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        conversation = [m for m in messages if m.get("role") != "system"]

        if len(conversation) <= 4:
            return messages

        # 保留最近 4 条
        recent = conversation[-4:]
        older = conversation[:-4]

        # 将旧消息压缩成一条摘要
        summary_parts = []
        for m in older:
            role = "用户" if m.get("role") == "user" else "助手"
            text = m.get("content", "")
            if isinstance(text, str) and len(text) > 100:
                text = text[:100] + "..."
            summary_parts.append(f"[{role}] {text}")

        summary = "（之前的对话摘要：\n" + "\n".join(summary_parts) + "\n）"

        return system_msgs + [{"role": "system", "content": summary}] + recent

    def _estimate_tokens(self, messages: list) -> float:
        text = " ".join(m.get("content", "") for m in messages)
        return len(re.findall(r"[\u4e00-\u9fff]", text)) * 1.5 + \
               len(re.findall(r"[a-zA-Z]", text)) * 0.25

    # ==================== 语义缓存 ====================

    def get_cache(self, messages: list, model: str) -> Optional[dict]:
        """查缓存"""
        cache_key = self._make_cache_key(messages, model)
        entry = self._cache.get(cache_key)
        if entry and entry["expires"] > datetime.now():
            logger.info("缓存命中")
            return entry["result"]
        elif entry:
            del self._cache[cache_key]
        return None

    def set_cache(self, messages: list, model: str, result: dict):
        """写缓存"""
        cache_key = self._make_cache_key(messages, model)
        self._cache[cache_key] = {
            "result": result,
            "expires": datetime.now() + timedelta(minutes=self.cache_ttl_minutes),
        }

    def _make_cache_key(self, messages: list, model: str) -> str:
        """生成缓存 key"""
        # 简单 hash：模型 + 消息内容
        content = model + "|" + json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

    def clear_cache(self):
        """清理过期缓存"""
        now = datetime.now()
        expired = [k for k, v in self._cache.items() if v["expires"] <= now]
        for k in expired:
            del self._cache[k]
        logger.info("清理 %d 条过期缓存", len(expired))

    def get_stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "cache_entries": len(self._cache),
            "cache_ttl_minutes": self.cache_ttl_minutes,
        }


# 需要 import json（前面用到了）
import json