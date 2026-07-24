"""
routes/models.py - 模型管理 API
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import httpx
import time

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models():
    from app import db
    return {"data": db.get_all_models()}


@router.get("/routing-groups")
async def list_routing_groups():
    from app import db
    return {"data": db.get_routing_groups()}


@router.post("/routing-groups")
async def create_routing_group(request: Request):
    from app import db
    body = await request.json()
    name = body.get("name", "")
    description = body.get("description", "")
    strategy = body.get("strategy", "ability")
    models = body.get("models", [])
    if not name:
        return JSONResponse({"error": "组名不能为空"}, status_code=400)
    gid = db.create_routing_group(name, description, strategy)
    if gid is None:
        return JSONResponse({"error": "组名已存在"}, status_code=409)
    if models:
        db.add_models_to_group(gid, models)
    return {"id": gid, "message": "路由组创建成功"}


@router.put("/routing-groups/{group_id}")
async def update_routing_group(group_id: int, request: Request):
    from app import db
    body = await request.json()
    if "name" in body:
        db.update_routing_group(group_id, name=body["name"])
    if "description" in body:
        db.update_routing_group(group_id, description=body["description"])
    if "strategy" in body:
        db.update_routing_group(group_id, strategy=body["strategy"])
    return {"message": "更新成功"}


@router.delete("/routing-groups/{group_id}")
async def delete_routing_group(group_id: int):
    from app import db
    db.delete_routing_group(group_id)
    return {"ok": True}


@router.post("/routing-groups/{group_id}/models")
async def add_models_to_group(group_id: int, request: Request):
    from app import db
    body = await request.json()
    models = body.get("models", [])
    db.add_models_to_group(group_id, models)
    return {"message": f"已添加 {len(models)} 个模型"}


@router.delete("/routing-groups/{group_id}/models/{model_id}")
async def remove_model_from_group(group_id: int, model_id: int):
    from app import db
    db.remove_model_from_group(group_id, model_id)
    return {"ok": True}


@router.post("/routing-groups/{group_id}/sync")
async def sync_routing_group_models(group_id: int):
    from app import db
    count = db.sync_models_to_group_from_provider(group_id)
    return {"synced": count}


@router.get("/{model_id}")
async def get_model(model_id: int):
    from app import db
    m = db.get_model(model_id)
    if not m:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    return {"data": m}


@router.put("/{model_id}")
async def update_model(model_id: int, request: Request):
    from app import db
    body = await request.json()
    kwargs = {}
    for field in ["display_name", "enabled", "ability_score"]:
        if field in body:
            kwargs[field] = body[field]
    if kwargs:
        db.update_model(model_id, **kwargs)
    return {"message": "更新成功"}


@router.post("/{model_id}/test")
async def test_model(model_id: int):
    from app import db
    m = db.get_model(model_id)
    if not m:
        return {"success": False, "error": "模型不存在"}
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
            return {"success": success, "latency_ms": latency, "status_code": resp.status_code}
    except Exception as e:
        db.update_model_sla(m["id"], False, 0)
        return {"success": False, "error": str(e)}