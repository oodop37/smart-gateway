"""
tasks.py - 共享后台任务函数
"""
import asyncio
import time
import httpx
from typing import List, Dict, Any

async def probe_all_models(db) -> int:
    """SLA 探针：测试所有启用模型的连通性，返回探针数量"""
    models = db.get_all_enabled_models_with_providers()
    if not models:
        return 0

    async def probe_one(m: Dict[str, Any]):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                t0 = time.time()
                resp = await client.post(
                    f"{m['base_url'].rstrip('/')}/chat/completions",
                    json={"model": m["model_name"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 2},
                    headers={"Authorization": f"Bearer {m['api_key']}"},
                )
                latency = int((time.time() - t0) * 1000)
                success = resp.is_success
                db.update_model_sla(m["id"], success, latency)
        except Exception:
            db.update_model_sla(m["id"], False, 0)

    await asyncio.gather(*[probe_one(m) for m in models])
    return len(models)