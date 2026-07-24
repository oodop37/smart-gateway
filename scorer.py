"""
scorer.py - 评分系统
排行榜爬虫 + 能力分映射 + 稳定分计算
"""

import re
import csv
import io
import logging
from datetime import datetime
from typing import Optional

import httpx

from constants import MODEL_ALIASES as MODEL_NAME_ALIASES


class LeaderboardScraper:
    """
    排行榜爬虫
    从多个来源爬取模型排行榜，获取 Elo 评分
    """

    def __init__(self, db, config: dict):
        self.db = db
        self.config = config.get("leaderboard", {})
        self.sources = self.config.get("sources", [])
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def scrape_all(self) -> int:
        """爬取所有排行榜源，返回新增条目数"""
        total = 0
        for source in self.sources:
            if not source.get("enabled", True):
                continue
            try:
                count = await self._scrape_source(source)
                total += count
                logger.info("排行榜[%s]: 新增 %d 条", source["name"], count)
            except Exception as e:
                logger.error("排行榜[%s] 爬取失败: %s", source["name"], e)
        return total

    async def _scrape_source(self, source: dict) -> int:
        """爬取单个排行榜源"""
        name = source["name"]
        url = source["url"]
        parser = source.get("parser", "csv")

        resp = await self.client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SmartGateway/1.0)"
        })
        resp.raise_for_status()

        count = 0
        if parser == "csv":
            count = self._parse_csv(name, resp.text)
        elif parser == "json":
            count = self._parse_json(name, resp.text)
        elif parser == "readme":
            count = self._parse_readme(name, resp.text)

        return count

    def _parse_csv(self, source: str, text: str) -> int:
        """解析 CSV 格式排行榜"""
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            # 尝试多种字段名（不同排行榜命名不同）
            model_name = (row.get("model") or row.get("Model") or
                          row.get("name") or row.get("Name") or "").strip()
            # Elo 字段：尝试多种可能
            elo_str = (row.get("elo") or row.get("Elo") or
                       row.get("elo_score") or row.get("arena_score") or
                       row.get("score") or row.get("Score") or
                       row.get("MT-bench (score)") or
                       row.get("MMLU") or "0")
            category = (row.get("category") or row.get("Category") or
                        row.get("type") or "general")

            if not model_name:
                continue

            try:
                elo = float(elo_str)
            except (ValueError, TypeError):
                elo = 0.0

            if elo > 0:
                self.db.save_leaderboard_entry(model_name, elo, category, source)
                count += 1

        return count

    def _parse_json(self, source: str, text: str) -> int:
        """解析 JSON 格式排行榜"""
        import json
        data = json.loads(text)
        count = 0

        # 支持数组或对象格式
        if isinstance(data, list):
            for item in data:
                model_name = (item.get("model") or item.get("Model") or
                              item.get("name") or "")
                elo = float(item.get("elo") or item.get("Elo") or
                            item.get("score") or 0)
                category = item.get("category", "general")
                if model_name and elo > 0:
                    self.db.save_leaderboard_entry(model_name, elo, category, source)
                    count += 1
        elif isinstance(data, dict):
            for model_name, score_data in data.items():
                if isinstance(score_data, dict):
                    elo = float(score_data.get("elo", 0))
                    category = score_data.get("category", "general")
                else:
                    elo = float(score_data) if score_data else 0
                    category = "general"
                if elo > 0:
                    self.db.save_leaderboard_entry(model_name, elo, category, source)
                    count += 1

        return count

    def _parse_readme(self, source: str, text: str) -> int:
        """从 README 中提取模型信息"""
        count = 0
        # 尝试匹配表格
        table_pattern = re.compile(r"\|(.+?)\|(.+?)\|(.+?)\|")
        lines = text.split("\n")
        in_table = False

        for line in lines:
            if re.match(r"^\s*\|.*\|.*\|.*\|", line):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    model_name = parts[0].strip()
                    elo = 0
                    # 尝试提取分数
                    for p in parts[1:]:
                        try:
                            elo = float(p)
                            break
                        except ValueError:
                            # 检查是不是模型名别名
                            pass
                    if elo > 0:
                        self.db.save_leaderboard_entry(model_name, elo, source=source)
                        count += 1

        return count


class ScoreSynchronizer:
    """
    评分同步器
    将排行榜分数映射到本地模型的能力分，并重新计算综合分
    """

    def __init__(self, db, config: dict):
        self.db = db
        self.scoring_config = config.get("scoring", {})
        self.ability_weight = self.scoring_config.get("ability_weight", 0.4)
        self.stability_weight = self.scoring_config.get("stability_weight", 0.6)
        self.stability_window = self.scoring_config.get("stability_window", 100)

    def sync_all(self) -> dict:
        """执行完整的评分同步流程"""
        # 1. 排行榜 → 能力分
        updated_ability = self.db.sync_ability_scores()

        # 2. 使用记录 → 稳定分
        self.db.update_stability_scores(window=self.stability_window)

        # 3. 综合分 = 能力分 × α + 稳定分 × β
        self.db.update_composite_scores(
            ability_weight=self.ability_weight,
            stability_weight=self.stability_weight,
        )

        return {
            "ability_scores_updated": updated_ability,
            "stability_scores_updated": True,
            "composite_scores_updated": True,
            "ability_weight": self.ability_weight,
            "stability_weight": self.stability_weight,
        }