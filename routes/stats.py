"""
routes/stats.py - 统计 & 发现 API
"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def get_stats(hours: int = 24):
    from app import db
    return db.get_stats(hours)


@router.post("/scores/sync")
async def sync_scores():
    from app import score_sync
    return score_sync.sync_all()


@router.get("/leaderboard")
async def get_leaderboard():
    from app import db
    return {"data": db.get_latest_leaderboard()}


@router.post("/leaderboard/refresh")
async def refresh_leaderboard():
    from app import scraper
    count = await scraper.scrape_all()
    return {"new_entries": count}


@router.post("/discovery/scan")
async def scan_discovery():
    from app import discoverer
    count = await discoverer.discover()
    return {"new_providers": count}


@router.post("/discovery/scan-candidates")
async def scan_candidates():
    from app import discoverer
    candidates = await discoverer.discover(dry_run=True)
    return {"data": candidates}


@router.post("/discovery/import-selected")
async def import_selected(request: Request):
    from app import db, discoverer
    body = await request.json()
    selected = body.get("selected", [])
    imported = 0
    for item in selected:
        name = item.get("name", "")
        base_url = item.get("base_url", "")
        if name and base_url:
            pid = db.add_provider(name, base_url, "")
            if pid is not None:
                imported += 1
    return {"imported": imported}


@router.post("/verify-provider")
async def verify_provider(request: Request):
    """验证供应商连通性（拉取模型列表）"""
    import httpx
    body = await request.json()
    base_url = body.get("base_url", "").rstrip("/")
    api_key = body.get("api_key", "")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.is_success:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                return {"ok": True, "models": models}
            return {"ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/sla/probe")
async def probe_all_models():
    """SLA 探针：测试所有启用模型的连通性（统一入口）"""
    from app import db
    import time
    models = db.get_all_enabled_models_with_providers()
    if not models:
        return {"probed": 0}

    async def probe_one(m):
        import httpx
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

    import asyncio
    await asyncio.gather(*[probe_one(m) for m in models])
    return {"probed": len(models)}