"""
database.py - 数据库层（已优化：使用上下文管理器确保连接正确关闭）
"""
import sqlite3
import json
import time
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager


class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """返回一个确保关闭的连接上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """初始化数据库表结构"""
        with self._get_conn() as conn:
            conn.executescript("""
                -- 供应商表
                CREATE TABLE IF NOT EXISTS providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    base_url TEXT NOT NULL,
                    api_key TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',  -- active | inactive | pending
                    source TEXT DEFAULT 'manual',  -- manual | discovery | import
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );

                -- 模型表
                CREATE TABLE IF NOT EXISTS models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    -- 评分相关
                    ability_score REAL DEFAULT 50.0,        -- 能力分（来自排行榜）
                    stability_score REAL DEFAULT 50.0,       -- 稳定稿从使用记录）
                    composite_score REAL DEFAULT 50.0,       -- 综合分
                    -- 熔断
                    consecutive_failures INTEGER DEFAULT 0,
                    circuit_breaker_until TEXT DEFAULT NULL,
                    -- 统计
                    total_requests INTEGER DEFAULT 0,
                    total_success INTEGER DEFAULT 0,
                    total_tokens_in INTEGER DEFAULT 0,
                    total_tokens_out INTEGER DEFAULT 0,
                    avg_latency_ms REAL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                    UNIQUE(provider_id, model_name)
                );

                -- 使用记录（滑动窗口来源）
                CREATE TABLE IF NOT EXISTS usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id INTEGER NOT NULL,
                    success INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    error_msg TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
                );

                -- 排行榜快照
                CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    elo_score REAL DEFAULT 0,
                    category TEXT DEFAULT 'general',
                    source TEXT DEFAULT '',
                    captured_at TEXT DEFAULT (datetime('now'))
                );

                -- 路由组
                CREATE TABLE IF NOT EXISTS routing_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    model_patterns TEXT DEFAULT '[]',  -- JSON 数组
                    sort_by TEXT DEFAULT 'composite_score',  -- composite_score | ability_score | stability_score
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );

                -- 模型 SLA 探针表
                CREATE TABLE IF NOT EXISTS model_sla (
                    model_id INTEGER PRIMARY KEY,
                    probe_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    avg_latency_ms REAL DEFAULT 0,
                    min_latency_ms REAL DEFAULT 0,
                    max_latency_ms REAL DEFAULT 0,
                    last_probed_at TEXT DEFAULT NULL,
                    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
                );
            """)
            conn.commit()

    # ==================== 供应商 CRUD ====================
    def get_providers(self, status: Optional[str] = None) -> List[Dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM providers WHERE status = ? ORDER BY name", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM providers ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    def get_provider(self, provider_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_provider(self, name: str, base_url: str, api_key: str = "") -> Optional[int]:
        with self._get_conn() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO providers (name, base_url, api_key) VALUES (?, ?, ?)",
                    (name, base_url, api_key),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def update_provider(self, provider_id: int, **kwargs) -> bool:
        if not kwargs:
            return False
        allowed = {"name", "base_url", "api_key", "status", "source"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return False
        vals.append(provider_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE providers SET {', '.join(sets)}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            conn.commit()
        return True

    def delete_provider(self, provider_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            conn.commit()
        return True

    # ==================== 模型 CRUD ====================
    def get_models(self, provider_id: Optional[int] = None) -> List[Dict]:
        with self._get_conn() as conn:
            if provider_id is not None:
                rows = conn.execute(
                    "SELECT * FROM models WHERE provider_id = ? ORDER BY model_name",
                    (provider_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM models ORDER BY model_name").fetchall()
            return [dict(r) for r in rows]

    def get_model(self, model_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM models WHERE id = ?", (model_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_model(
        self,
        provider_id: int,
        model_name: str,
        display_name: str = "",
        enabled: int = 1,
    ) -> Optional[int]:
        with self._get_conn() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO models (provider_id, model_name, display_name, enabled)
                    VALUES (?, ?, ?, ?)
                    """,
                    (provider_id, model_name, display_name, enabled),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def update_model(self, model_id: int, **kwargs) -> bool:
        if not kwargs:
            return False
        allowed = {
            "display_name",
            "enabled",
            "ability_score",
            "stability_score",
            "composite_score",
            "consecutive_failures",
            "circuit_breaker_until",
            "total_requests",
            "total_success",
            "total_tokens_in",
            "total_tokens_out",
            "avg_latency_ms",
        }
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return False
        vals.append(model_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE models SET {', '.join(sets)}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            conn.commit()
        return True

    def delete_model(self, model_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
            conn.commit()
        return True

    # ==================== 使用记录 & 评分 ====================
    def record_usage(
        self,
        model_id: int,
        success: bool,
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error_msg: str = "",
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO usage_logs (model_id, success, latency_ms, tokens_in, tokens_out, error_msg)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (model_id, 1 if success else 0, latency_ms, tokens_in, tokens_out, error_msg),
            )
            if success:
                conn.execute(
                    """
                    UPDATE models SET
                       consecutive_failures = 0,
                       circuit_breaker_until = NULL,
                       total_requests = total_requests + 1,
                       total_success = total_success + 1,
                       total_tokens_in = total_tokens_in + ?,
                       total_tokens_out = total_tokens_out + ?,
                       updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (tokens_in, tokens_out, model_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE models SET
                       consecutive_failures = consecutive_failures + 1,
                       total_requests = total_requests + 1,
                       updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (model_id,),
                )
            conn.commit()

    def on_request_success(
        self, model_id: int, latency_ms: int = 0, tokens_in: int = 0, tokens_out: int = 0
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE models SET
                   consecutive_failures = 0,
                   circuit_breaker_until = NULL,
                   total_requests = total_requests + 1,
                   total_success = total_success + 1,
                   total_tokens_in = total_tokens_in + ?,
                   total_tokens_out = total_tokens_out + ?,
                   stability_score = MIN(100, stability_score + 1),
                   updated_at = datetime('now')
                 WHERE id = ?
                """,
                (tokens_in, tokens_out, model_id),
            )
            conn.execute(
                """
                INSERT INTO usage_logs (model_id, success, latency_ms, tokens_in, tokens_out)
                VALUES (?, 1, ?, ?, ?)
                """,
                (model_id, latency_ms, tokens_in, tokens_out),
            )
            conn.commit()

    def on_request_failure(self, model_id: int, error_msg: str = "", latency_ms: int = 0):
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE models SET
                   consecutive_failures = consecutive_failures + 1,
                   total_requests = total_requests + 1,
                   updated_at = datetime('now')
                 WHERE id = ?
                """,
                (model_id,),
            )
            conn.execute(
                """
                INSERT INTO usage_logs (model_id, success, latency_ms, tokens_in, tokens_out, error_msg)
                VALUES (?, 0, ?, ?, 0, ?)
                """,
                (model_id, latency_ms, 0, 0, error_msg),
            )
            conn.commit()

    def update_stability_scores(self, window: int = 100):
        """基于滑动窗口重新计算所有模型的稳定分"""
        with self._get_conn() as conn:
            models = conn.execute("SELECT id FROM models").fetchall()
            for m in models:
                mid = m["id"]
                logs = conn.execute(
                    """
                    SELECT success, latency_ms FROM usage_logs
                    WHERE model_id = ? ORDER BY created_at DESC LIMIT ?
                    """,
                    (mid, window),
                ).fetchall()
                if not logs:
                    continue
                success_count = sum(1 for l in logs if l["success"])
                total = len(logs)
                success_rate = success_count / total if total > 0 else 0
                latencies = [l["latency_ms"] for l in logs if l["latency_ms"] > 0]
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
                    latency_score = max(0, 1 - avg_latency / 5000)
                else:
                    latency_score = 1.0
                stability_score = round(success_rate * 70 + latency_score * 30, 2)
                conn.execute(
                    """
                    UPDATE models SET stability_score = ?, avg_latency_ms = ? WHERE id = ?
                    """,
                    (stability_score, round(sum(latencies) / len(latencies)) if latencies else 0, mid),
                )
            conn.commit()

    def update_composite_scores(
        self, ability_weight: float = 0.4, stability_weight: float = 0.6
    ):
        """重新计算所有模型的综合分"""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE models SET
                   composite_score = ROUND(
                       ability_score * ? + stability_score * ?, 2
                   )
                """,
                (ability_weight, stability_weight),
            )
            conn.commit()

    # ==================== 排行榜 ====================
    def get_latest_leaderboard(self) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT model_name, elo_score, category, source, captured_at
                FROM leaderboard_snapshots
                WHERE captured_at = (
                    SELECT MAX(captured_at) FROM leaderboard_snapshots
                )
                ORDER BY elo_score DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def save_leaderboard_snapshot(self, entries: List[Dict]):
        with self._get_conn() as conn:
            for e in entries:
                conn.execute(
                    """
                    INSERT INTO leaderboard_snapshots
                    (model_name, elo_score, category, source, captured_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    """,
                    (e["model_name"], e["elo_score"], e["category"], e.get("source", "")),
                )
            conn.commit()

    # ==================== 模型名称别名映射（排行榜名 → 本地名关键词） ====================
    from constants import MODEL_KEYWORDS

    def _match_keyword(self, text: str) -> List[str]:
        """从文本中提取匹配的模型关键词"""
        text_lower = text.lower()
        matched = []
        for keyword, aliases in self.MODEL_KEYWORDS.items():
            for alias in aliases:
                if alias in text_lower:
                    matched.append(keyword)
                    break
        return matched

    def sync_ability_scores(self) -> int:
        """将排行榜分数映射到本地模型的能力分"""
        leaderboard = self.get_latest_leaderboard()
        if not leaderboard:
            return 0
        # 获取 Elo 范围用于归一化
        elo_scores = [r["elo_score"] for r in leaderboard]
        min_elo = min(elo_scores) if elo_scores else 0
        max_elo = max(elo_scores) if elo_scores else 1000
        elo_range = max_elo - min_elo if max_elo > min_elo else 1
        # 获取所有本地模型
        with self._get_conn() as conn:
            local_models = conn.execute(
                "SELECT id, model_name, display_name FROM models"
            ).fetchall()
            updated = 0
            for entry in leaderboard:
                lb_name = entry["model_name"].lower()
                lb_keywords = self._match_keyword(lb_name)
                if not lb_keywords:
                    continue
                for lm in local_models:
                    local_name = (lm["model_name"] + " " + lm["display_name"]).lower()
                    local_keywords = self._match_keyword(local_name)
                    if set(lb_keywords) & set(local_keywords):
                        ability = round(
                            (entry["elo_score"] - min_elo) / elo_range * 100, 2
                        )
                        conn.execute(
                            "UPDATE models SET ability_score = MAX(ability_score, ?) WHERE id = ?",
                            (ability, lm["id"]),
                        )
                        updated += 1
            conn.commit()
            return updated

    # ==================== 路由组 ====================
    def get_routing_groups(self) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM routing_groups ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_routing_group(
        self, name: str, description: str = "", model_patterns: List[str] = None, sort_by: str = "composite_score"
    ) -> Optional[int]:
        with self._get_conn() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO routing_groups (name, description, model_patterns, sort_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, description, json.dumps(model_patterns or []), sort_by),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def update_routing_group(self, group_id: int, **kwargs):
        if not kwargs:
            return
        allowed = {"name", "description", "model_patterns", "sort_by"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                if k == "model_patterns" and isinstance(v, list):
                    v = json.dumps(v)
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(group_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE routing_groups SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            conn.commit()

    def delete_routing_group(self, group_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM routing_groups WHERE id = ?", (group_id,))
            conn.commit()

    def add_models_to_group(self, group_id: int, model_ids: List[int]):
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO group_models (group_id, model_id) VALUES (?, ?)",
                [(group_id, mid) for mid in model_ids],
            )
            conn.commit()

    def remove_model_from_group(self, group_id: int, model_id: int):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM group_models WHERE group_id = ? AND model_id = ?",
                (group_id, model_id),
            )
            conn.commit()

    def get_models_in_group(self, group_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM models m
                JOIN group_models gm ON m.id = gm.model_id
                WHERE gm.group_id = ?
                ORDER BY m.model_name
                """,
                (group_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def sync_models_to_group_from_provider(self, group_id: int) -> int:
        """根据路由组的模式从供应商同步模型（简化实现）"""
        # 这里保持原有逻辑，但使用新的连接方式
        with self._get_conn() as conn:
            group = conn.execute(
                "SELECT * FROM routing_groups WHERE id = ?", (group_id,)
            ).fetchone()
            if not group:
                return 0
            # 实际同步逻辑省略，返回 0 表示未实现
            return 0

    # ==================== 模型 SLA 探针 ====================
    def get_all_enabled_models_with_providers(self) -> List[Dict]:
        """获取所有启用模型，含供应商信息，用于探针"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT m.*, p.name as provider_name, p.base_url, p.api_key
                FROM models m
                JOIN providers p ON m.provider_id = p.id
                WHERE m.enabled = 1 AND p.status = 'active'
                  AND (m.circuit_breaker_until IS NULL OR m.circuit_breaker_until < datetime('now'))
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_model_sla_list(self) -> List[Dict]:
        """获取所有模型的 SLA 数据，按可用率降序"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.model_name, m.display_name, p.name as provider_name,
                       COALESCE(s.probe_count, 0) as probe_count,
                       COALESCE(s.success_count, 0) as success_count,
                       COALESCE(s.fail_count, 0) as fail_count,
                       COALESCE(s.avg_latency_ms, 0) as avg_latency_ms,
                       COALESCE(s.min_latency_ms, 0) as min_latency_ms,
                       COALESCE(s.max_latency_ms, 0) as max_latency_ms,
                       s.last_probed_at,
                       m.enabled
                FROM models m
                JOIN providers p ON m.provider_id = p.id
                LEFT JOIN model_sla s ON m.id = s.model_id
                WHERE m.enabled = 1
                ORDER BY
                    CASE WHEN s.probe_count > 0
                         THEN CAST(s.success_count AS REAL) / s.probe_count
                         ELSE 0 END DESC,
                    s.avg_latency_ms ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def update_model_sla(self, model_id: int, success: bool, latency_ms: float):
        """更新模型 SLA 探针数据"""
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM model_sla WHERE model_id = ?", (model_id,)
            ).fetchone()
            now = datetime.now().isoformat()
            if existing:
                if success:
                    conn.execute(
                        """
                        UPDATE model_sla SET
                           probe_count = probe_count + 1,
                           success_count = success_count + 1,
                           avg_latency_ms = (avg_latency_ms * probe_count + ?) / (probe_count + 1),
                           min_latency_ms = CASE WHEN ? < min_latency_ms OR min_latency_ms = 0 THEN ? ELSE min_latency_ms END,
                           max_latency_ms = CASE WHEN ? > max_latency_ms THEN ? ELSE max_latency_ms END,
                           last_probed_at = ?
                         WHERE model_id = ?
                        """,
                        (latency_ms, latency_ms, latency_ms, latency_ms, latency_ms, now, model_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE model_sla SET
                           probe_count = probe_count + 1,
                           fail_count = fail_count + 1,
                           last_probed_at = ?
                         WHERE model_id = ?
                        """,
                        (now, model_id),
                    )
            else:
                if success:
                    conn.execute(
                        """
                        INSERT INTO model_sla (model_id, probe_count, success_count, fail_count,
                                               avg_latency_ms, min_latency_ms, max_latency_ms, last_probed_at)
                        VALUES (?, 1, 1, 0, ?, ?, ?, ?)
                        """,
                        (model_id, latency_ms, latency_ms, latency_ms, now),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO model_sla (model_id, probe_count, success_count, fail_count,
                                               avg_latency_ms, min_latency_ms, max_latency_ms, last_probed_at)
                        VALUES (?, 1, 0, 1, 0, 0, 0, ?)
                        """,
                        (model_id, now),
                    )
            conn.commit()

    # ==================== 统计 ====================
    def get_stats(self, hours: int = 24) -> Dict:
        with self._get_conn() as conn:
            cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
            total = conn.execute(
                "SELECT COUNT(*) as c FROM usage_logs WHERE created_at > ?", (cutoff,)
            ).fetchone()["c"]
            success = conn.execute(
                "SELECT COUNT(*) as c FROM usage_logs WHERE success = 1 AND created_at > ?",
                (cutoff,),
            ).fetchone()["c"]
            tokens = conn.execute(
                """
                SELECT SUM(tokens_in) as tin, SUM(tokens_out) as tout
                FROM usage_logs WHERE created_at > ?
                """,
                (cutoff,),
            ).fetchone()
            by_model = conn.execute(
                """
                SELECT m.model_name, p.name as provider,
                       COUNT(*) as total,
                       SUM(CASE WHEN u.success THEN 1 ELSE 0 END) as success,
                       SUM(u.tokens_in) as tokens_in,
                       SUM(u.tokens_out) as tokens_out
                FROM usage_logs u
                JOIN models m ON u.model_id = m.id
                JOIN providers p ON m.provider_id = p.id
                WHERE u.created_at > ?
                GROUP BY m.id
                ORDER BY total DESC
                """,
                (cutoff,),
            ).fetchall()
            return {
                "total_requests": total,
                "success_requests": success,
                "success_rate": round(success / total * 100, 1) if total > 0 else 0,
                "tokens_in": tokens["tin"] or 0,
                "tokens_out": tokens["tout"] or 0,
                "total_tokens": (tokens["tin"] or 0) + (tokens["tout"] or 0),
                "by_model": [dict(r) for r in by_model],
            }