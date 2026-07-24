"""
scheduler.py - 定时任务
排行榜刷新 + GitHub 自动发现 + 评分巡检 + 模型 SLA 探针
"""
import asyncio
import logging
import time
from datetime import datetime
import httpx

logger = logging.getLogger("smart-gateway.scheduler")


class Scheduler:
    """定时任务调度器
    排行榜刷新 + GitHub 自动发现 + 评分巡检 + 模型 SLA 探针
    """

    def __init__(self, db, scraper, score_sync, discoverer, config: dict):
        self.db = db
        self.scraper = scraper
        self.score_sync = score_sync
        self.discoverer = discoverer
        self.config = config
        self._tasks = []
        self._running = False

    async def start(self):
        """启动所有定时任务"""
        self._running = True
        lb_config = self.config.get("leaderboard", {})
        disc_config = self.config.get("discovery", {})

        if lb_config.get("enabled", True):
            lb_interval = lb_config.get("interval_minutes", 360)
            self._tasks.append(
                asyncio.create_task(
                    self._loop("排行榜刷新", self._refresh_leaderboard, lb_interval)
                )
            )
            logger.info("排行榜刷新任务已启动，间隔 %d 分钟", lb_interval)

        if disc_config.get("enabled", False):
            disc_interval = disc_config.get("interval_minutes", 1440)
            self._tasks.append(
                asyncio.create_task(
                    self._loop("GitHub发现", self._discover_providers, disc_interval)
                )
            )
            logger.info("GitHub发现任务已启动，间隔 %d 分钟", disc_interval)

        # 评分巡检：每 5 分钟执行一次
        self._tasks.append(
            asyncio.create_task(
                self._loop("评分巡检", self._sync_scores, 5)
            )
        )
        logger.info("评分巡检任务已启动，间隔 5 分钟")

        # 模型 SLA 探针：每 1 分钟执行一次
        sla_interval = self.config.get("sla", {}).get("interval_minutes", 1)
        self._tasks.append(
            asyncio.create_task(
                self._loop("模型SLA探针", self._probe_models, sla_interval)
            )
        )
        logger.info("模型SLA探针任务已启动，间隔 %d 分钟", sla_interval)

    async def stop(self):
        """停止所有任务"""
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    async def _loop(self, name: str, func, interval_minutes: int):
        """通用循环"""
        try:
            await func()
        except Exception as e:
            logger.error("%s 首次执行失败: %s", name, e)

        while self._running:
            await asyncio.sleep(interval_minutes * 60)
            try:
                await func()
            except Exception as e:
                logger.error("%s 执行失败: %s", name, e)

    async def _refresh_leaderboard(self):
        """刷新排行榜"""
        logger.info("🔄 开始刷新排行榜...")
        count = await self.scraper.scrape_all()
        logger.info("✅ 排行榜刷新完成，新增 %d 条", count)

    async def _discover_providers(self):
        """GitHub 自动发现新供应商"""
        logger.info("🔄 开始扫描 GitHub 发现新供应商...")
        count = await self.discoverer.discover()
        logger.info("✅ GitHub 发现完成，新增 %d 个供应商", count)

    async def _sync_scores(self):
        """评分巡检"""
        logger.info("🔄 执行评分同步...")
        result = self.score_sync.sync_all()
        logger.info("✅ 评分同步完成: %s", result)

    async def _probe_models(self):
        """模型 SLA 探针：对所有启用模型测试连通性"""
        from tasks import probe_all_models
        count = await probe_all_models(self.db)
        logger.info("✅ 模型 SLA 探针完成，探针 %d 个模型", count)