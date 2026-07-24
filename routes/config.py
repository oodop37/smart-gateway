"""
routes/config.py - 配置管理 API
"""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config():
    from app import config, compressor
    return {
        "scoring": config.get("scoring", {}),
        "compression": compressor.get_stats(),
        "leaderboard": config.get("leaderboard", {}),
        "discovery": config.get("discovery", {}),
    }


@router.post("/config/scoring")
async def update_scoring(request: Request):
    from app import db, config
    body = await request.json()
    config["scoring"]["ability_weight"] = body.get("ability_weight", 0.4)
    config["scoring"]["stability_weight"] = body.get("stability_weight", 0.6)
    db.update_composite_scores(
        config["scoring"]["ability_weight"],
        config["scoring"]["stability_weight"],
    )
    return {"message": "评分权重已更新"}


@router.post("/config/compression")
async def update_compression(request: Request):
    from app import config, compressor
    body = await request.json()
    if "enabled" in body:
        config.setdefault("compression", {})["enabled"] = body["enabled"]
        compressor.enabled = body["enabled"]
    if "mode" in body:
        config["compression"]["mode"] = body["mode"]
        compressor.mode = body["mode"]
    if "max_context_tokens" in body:
        config["compression"]["max_context_tokens"] = body["max_context_tokens"]
        compressor.max_context_tokens = body["max_context_tokens"]
    if "cache_ttl_minutes" in body:
        config["compression"]["cache_ttl_minutes"] = body["cache_ttl_minutes"]
        compressor.cache_ttl_minutes = body["cache_ttl_minutes"]
    return {"message": "压缩配置已更新", "stats": compressor.get_stats()}