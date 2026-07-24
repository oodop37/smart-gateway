"""
routes/providers.py - 供应商管理 API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = []


@router.get("")
async def get_providers(status: str | None = None):
    from app import db
    return {"data": db.get_providers(status)}


@router.get("/{provider_id}")
async def get_provider(provider_id: int):
    from app import db
    p = next((x for x in db.get_providers() if x["id"] == provider_id), None)
    if not p:
        return JSONResponse({"error": "供应商不存在"}, status_code=404)
    return {"data": p}


@router.post("")
async def add_provider(p: ProviderIn):
    from app import db
    pid = db.add_provider(p.name, p.base_url, p.api_key)
    if pid is None:
        return JSONResponse({"error": "供应商已存在"}, status_code=409)
    if p.models:
        db.batch_add_models(pid, p.models)
    return {"id": pid, "message": "供应商添加成功"}


@router.put("/{provider_id}")
async def update_provider(provider_id: int, request: Request):
    from app import db
    body = await request.json()
    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "base_url" in body:
        kwargs["base_url"] = body["base_url"]
    if kwargs:
        db.update_provider(provider_id, **kwargs)
    return {"message": "更新成功"}


@router.delete("/{provider_id}")
async def delete_provider(provider_id: int):
    from app import db, config, SmartRouter
    import app as app_module
    try:
        db.delete_provider(provider_id)
        app_module.router = SmartRouter(db, config)
        return {"ok": True, "message": "供应商已删除"}
    except Exception as e:
        return JSONResponse({"error": f"删除失败: {str(e)}"}, status_code=500)


@router.get("/{provider_id}/keys")
async def get_provider_keys(provider_id: int):
    from app import db
    keys = db.get_provider_keys(provider_id)
    for k in keys:
        v = k.get("key_value", "")
        if len(v) > 8:
            k["masked"] = v[:4] + "*" * (len(v) - 8) + v[-4:]
    return {"data": keys}


@router.post("/{provider_id}/keys")
async def add_provider_key(provider_id: int, request: Request):
    from app import db
    body = await request.json()
    key_name = body.get("key_name", "")
    key_value = body.get("key_value", "")
    key_id = db.add_provider_key(provider_id, key_name, key_value)
    return {"id": key_id, "message": "Key 添加成功"}


@router.delete("/{provider_id}/keys/{key_id}")
async def delete_provider_key(provider_id: int, key_id: int):
    from app import db
    db.delete_provider_key(key_id)
    return {"ok": True}


@router.post("/validate-key")
async def validate_provider_key(request: Request):
    """验证单个 key 是否可用，返回可用模型列表"""
    from app import db
    import httpx
    body = await request.json()
    key_value = body.get("key_value", "").strip()
    if not key_value:
        return {"ok": False, "error": "key 不能为空"}
    # 从 db 中找到 key 对应的供应商
    rows = db.get_all_provider_keys()
    row = next((r for r in rows if r.get("key_value") == key_value), None)
    if not row:
        return {"ok": False, "error": "key 未找到"}
    base_url = row["base_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key_value}"},
            )
        if resp.is_success:
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", [])]
            return {"ok": True, "models": models, "provider": row.get("provider_name")}
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/batch-import")
async def batch_import_providers(request: Request):
    from app import db
    body = await request.json()
    providers = body.get("providers", [])
    imported = 0
    for p in providers:
        name = p.get("name", "")
        base_url = p.get("base_url", "")
        api_key = p.get("api_key", "")
        if name and base_url:
            pid = db.add_provider(name, base_url, api_key)
            if pid is not None:
                imported += 1
    return {"imported": imported}