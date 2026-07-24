"""
discoverer.py - GitHub 自动发现
扫描 GitHub 上的免费 API 仓库，自动发现新供应商
"""

import re
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger("smart-gateway.discoverer")


class ProviderDiscoverer:
    """
    供应商自动发现器
    扫描 GitHub 仓库，提取免费 API 信息，生成模板
    """

    # 免费 API 仓库配置
    DEFAULT_REPOS = [
        {
            "url": "https://api.github.com/repos/chatanywhere/GPT_API_free/readme",
            "parser": "readme",
            "name": "chatanywhere",
        },
        {
            "url": "https://api.github.com/repos/LLM-Red-Team/awesome-free-chatgpt/readme",
            "parser": "readme",
            "name": "awesome-free",
        },
        {
            "url": "https://api.github.com/repos/PawanOsman/ChatGPT/readme",
            "parser": "readme",
            "name": "pawan",
        },
    ]

    # 常见免费 API 标签
    FREE_API_PATTERNS = [
        r"(https?://[^\s]+(?:v1|api)[^\s]*)",
        r"base[_-]?url[:\s]+['\"]?(https?://[^\s]+)['\"]?",
        r"api[_-]?endpoint[:\s]+['\"]?(https?://[^\s]+)['\"]?",
    ]

    # 已知免费供应商模板（静态兜底，即使 GitHub 爬不到也能用）
    FALLBACK_PROVIDERS = [
        {
            "name": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "models": [
                "deepseek-ai/deepseek-v4-flash",
                "deepseek-ai/deepseek-r1",
                "qwen/qwen3.5-397b-awq",
                "google/gemma-3-27b-it",
                "meta/llama-4-maverick-17b-128e-instruct",
                "mistralai/mistral-small-3.1-24b-instruct-2503",
            ],
            "api_key_hint": "https://build.nvidia.com/explore/discover",
        },
        {
            "name": "modelscope",
            "base_url": "https://api-inference.modelscope.cn/v1",
            "models": [
                "Qwen/Qwen3.5-397B-AWQ",
                "deepseek-ai/DeepSeek-V4-0514",
                "ZhipuAI/GLM-5.2-240B-0414",
                "deepseek-ai/DeepSeek-R1",
            ],
            "api_key_hint": "https://modelscope.cn/my/myAccessKey",
        },
        {
            "name": "sensetime",
            "base_url": "https://api.sensenova.cn/v1",
            "models": [
                "SenseChat-5.5-Plus",
                "SenseChat-5.5",
            ],
            "api_key_hint": "https://console.sensecore.cn/",
        },
        {
            "name": "google-gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "models": [
                "gemini-2.5-flash",
                "gemini-2.5-flash-8b",
                "gemini-2.5-pro",
            ],
            "api_key_hint": "https://aistudio.google.com/apikey",
        },
        {
            "name": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "models": [
                "deepseek-r1-distill-llama-70b",
                "llama-4-scout-17b-16e-instruct",
                "llama-3.3-70b-versatile",
                "mixtral-8x7b-32768",
            ],
            "api_key_hint": "https://console.groq.com/keys",
        },
        {
            "name": "together",
            "base_url": "https://api.together.xyz/v1",
            "models": [
                "deepseek-ai/DeepSeek-V4",
                "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                "meta-llama/Llama-4-Scout-17B-16E-Instruct",
                "Qwen/Qwen3.5-397B-AWQ",
            ],
            "api_key_hint": "https://api.together.xyz/settings/api-keys",
        },
        {
            "name": "fireworks",
            "base_url": "https://api.fireworks.ai/inference/v1",
            "models": [
                "accounts/fireworks/models/deepseek-v4",
                "accounts/fireworks/models/qwen3.5-397b",
                "accounts/fireworks/models/llama-v4-maverick",
            ],
            "api_key_hint": "https://fireworks.ai/api-keys",
        },
        {
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "models": [
                "deepseek/deepseek-v4",
                "qwen/qwen-3.5-397b-awq",
                "meta-llama/llama-4-maverick",
                "google/gemini-2.5-flash",
            ],
            "api_key_hint": "https://openrouter.ai/keys",
        },
    ]

    def __init__(self, db, config: dict):
        self.db = db
        self.config = config.get("discovery", {})
        self.repos = self.config.get("repos", [])
        self.client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "SmartGateway/1.0"},
        )

    async def close(self):
        await self.client.aclose()

    async def discover(self, dry_run: bool = False):
        """
        执行发现流程
        dry_run=False: 写入本地，返回新增数量(int)
        dry_run=True: 只返回候选列表(list)，不写入本地
        """
        existing_names = {p["name"] for p in self.db.get_providers()}
        candidates = []

        # 从 GitHub 发现
        for repo in self.repos:
            try:
                found = await self._scrape_repo(repo)
                candidates.extend(found)
            except Exception as e:
                logger.warning("GitHub发现[%s] 失败: %s", repo.get("url"), e)

        # 兜底：静态供应商
        for provider in self.FALLBACK_PROVIDERS:
            if provider["name"] not in existing_names:
                candidates.append({
                    "name": provider["name"],
                    "base_url": provider["base_url"],
                    "models": provider["models"],
                    "api_key_hint": provider.get("api_key_hint", ""),
                    "source": "discovery",
                })

        if dry_run:
            return candidates

        # 写入本地
        discovered = 0
        for c in candidates:
            pid = self.db.add_provider(
                name=c["name"],
                base_url=c["base_url"],
                api_key="",
                status="active",
                source="discovery",
            )
            if pid and c.get("models"):
                self.db.batch_add_models(pid, c["models"])
                discovered += 1
        return discovered

    async def _scrape_repo(self, repo: dict):
        """爬取单个 GitHub 仓库，返回候选列表"""
        url = repo.get("url")
        if not url:
            return []

        raw_url = url
        if "github.com" in url and "/blob/" in url:
            raw_url = url.replace("github.com", "raw.githubusercontent.com")
            raw_url = raw_url.replace("/blob/", "/")

        resp = await self.client.get(raw_url)
        resp.raise_for_status()
        content = resp.text

        parser = repo.get("parser", "readme")
        if parser == "readme":
            return self._parse_readme(content)
        return []

    def _parse_readme(self, content: str):
        """解析 README 提取 API 供应商，返回候选列表（不写 DB）"""
        candidates = []
        seen = set()
        existing_names = {p["name"] for p in self.db.get_providers()}

        for pattern in self.FREE_API_PATTERNS:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for url in matches:
                url = url.strip().strip("'\"")
                if not url.startswith("http"):
                    continue
                provider_name = self._extract_provider_name(url)
                if provider_name in seen or provider_name in existing_names:
                    continue
                seen.add(provider_name)
                candidates.append({
                    "name": provider_name,
                    "base_url": url,
                    "models": [],
                    "api_key_hint": "",
                    "source": "discovery",
                })
                logger.info("📦 发现新供应商候选: %s (%s)", provider_name, url)

        return candidates

    def _extract_provider_name(self, url: str) -> str:
        """从 URL 中提取供应商名"""
        # 尝试从域名提取
        match = re.search(r"//([^.]+)\.", url)
        if match:
            name = match.group(1)
            # 清理
            name = re.sub(r"[^a-zA-Z0-9_-]", "", name)
            if name:
                return name.lower()

        # 兜底：用 URL hash
        return f"provider_{hash(url) % 10000}"