"""
app.py - Smart Gateway 主程序
OpenAI 兼容接口 + 管理面板（路由已拆分到 routes/）
"""
import os
import sys
import logging
import time
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
from typing import Optional

# ==================== 本项目模块 ====================
from database import Database
from router import SmartRouter
from scorer import LeaderboardScraper, ScoreSynchronizer
from discoverer import ProviderDiscoverer
from compressor import ContextCompressor
from scheduler import Scheduler
from routes import providers_router, models_router, stats_router, config_router

# ==================== 配置 ====================
BASE_DIR = Path(__file__).parent
CONFIG_PATH = os.environ.get("CONFIG_PATH", str(BASE_DIR / "config.yaml"))

import yaml
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

logging.basicConfig(
    level=getattr(logging, config.get("logging", {}).get("level", "INFO")),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("smart-gateway")

# ==================== Prometheus 监控 ====================
_metrics_registry = CollectorRegistry()

HTTP_REQ_COUNT = Counter(
    "smart_gateway_requests_total", "Total HTTP requests",
    ["method", "endpoint", "status"], registry=_metrics_registry,
)
HTTP_REQ_LATENCY = Histogram(
    "smart_gateway_latency_seconds", "Request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=_metrics_registry,
)
HTTP_ACTIVE_COUNT = Gauge(
    "smart_gateway_active", "Currently active requests",
    registry=_metrics_registry,
)
MODEL_ROUTING_COUNT = Counter(
    "smart_gateway_routing_total", "Model routing count",
    ["provider", "model", "status"], registry=_metrics_registry,
)

# ==================== Rate Limiter ====================
limiter = Limiter(key_func=get_remote_address)

# ==================== 全局对象 ====================
db = Database(os.environ.get("DB_PATH", config["database"]["path"]))
router = SmartRouter(db, config)
scraper = LeaderboardScraper(db, config)
score_sync = ScoreSynchronizer(db, config)
discoverer = ProviderDiscoverer(db, config)
compressor = ContextCompressor(db, config)
scheduler = Scheduler(db, scraper, score_sync, discoverer, config)


# ==================== 生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Smart Gateway 启动中...")
    await scheduler.start()
    logger.info("✅ Smart Gateway 已启动 → http://0.0.0.0:%d", config["port"])
    yield
    logger.info("👋 Smart Gateway 关闭中...")
    await scheduler.stop()
    await router.close()
    await scraper.close()
    await discoverer.close()
    logger.info("✅ Smart Gateway 已关闭")

app = FastAPI(title="Smart Gateway", version="1.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ==================== 中间件 ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 注册路由模块 ====================
app.include_router(providers_router)
app.include_router(models_router)
app.include_router(stats_router)
app.include_router(config_router)

# ==================== 静态文件 ====================
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ==================== 请求监控中间件 ====================
@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    if request.url.path in ("/metrics", "/health"):
        return await call_next(request)
    HTTP_ACTIVE_COUNT.inc()
    t0 = time.time()
    status = 200
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500
        raise
    finally:
        HTTP_ACTIVE_COUNT.dec()
        latency = time.time() - t0
        HTTP_REQ_LATENCY.labels(request.method, request.url.path).observe(latency)
        HTTP_REQ_COUNT.labels(request.method, request.url.path, str(status)).inc()


# ==================== OpenAI 兼容接口 ====================
_model_list_cache = {"data": None, "expires": 0}


async def _build_model_list():
    """构建 /v1/models 响应（统一逻辑，删除 router.py 中的重复副本）"""
    import time as _time
    global _model_list_cache
    if _model_list_cache["data"] is None or _time.time() > _model_list_cache["expires"]:
        candidates = db.get_available_models()
        result = []
        seen = set()
        for c in candidates:
            model_id = f"{c['provider_name']}/{c['model_name']}"
            if model_id not in seen:
                seen.add(model_id)
                result.append({
                    "id": model_id,
                    "object": "model",
                    "created": int(_time.time()),
                    "owned_by": c["provider_name"],
                    "composite_score": c["composite_score"],
                })
        # 路由组作为虚拟模型
        try:
            for grp in db.get_routing_groups():
                if grp["name"] not in seen:
                    seen.add(grp["name"])
                    result.append({
                        "id": grp["name"],
                        "object": "model",
                        "created": int(_time.time()),
                        "owned_by": "routing",
                        "composite_score": 0,
                        "routing_group": True,
                    })
        except Exception:
            pass
        _model_list_cache["data"] = result
        _model_list_cache["expires"] = _time.time() + 60
    return _model_list_cache["data"]


class ChatRequest(BaseModel):
    model: str
    messages: list
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None


@app.get("/v1/models")
async def list_models_endpoint():
    return {"object": "list", "data": await _build_model_list()}


@app.post("/v1/chat/completions")
@limiter.limit("60/minute")
async def chat_completions(request: Request, req: ChatRequest):
    """OpenAI 兼容聊天补全（流式/非流式）"""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    model = req.model
    messages = req.messages

    # 路由组 → 查询组内模型
    if model and not any(m["model_name"] == model for m in db.get_available_models()):
        groups = db.get_routing_groups()
        if any(g["name"] == model for g in groups):
            candidates = db.get_available_models(model)
        else:
            candidates = db.get_available_models()
    else:
        candidates = db.get_available_models(model)

    if not candidates:
        return JSONResponse({
            "error": {
                "message": f"No available models for '{model}'. "
                           "All models are either disabled or circuit-broken.",
                "type": "router_error",
                "code": 503,
            }
        }, status_code=503)

    # 上下文压缩
    if not req.stream and compressor.enabled:
        messages = compressor.compress_messages(messages)
        cache_key_model = model
        cached = compressor.get_cache(messages, cache_key_model)
        if cached:
            return JSONResponse(cached)

    extra = {}
    for field in ["temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty"]:
        val = getattr(req, field)
        if val is not None:
            extra[field] = val

    if req.stream:
        generator = await router.route_chat_completion(model, messages, stream=True, **extra)
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        result = await router.route_chat_completion(model, messages, stream=False, **extra)
        if "error" in result:
            return JSONResponse(result, status_code=result["error"].get("code", 503))
        if compressor.enabled:
            compressor.set_cache(messages, model, result)
        return JSONResponse(result)


# ==================== 管理面板 ====================
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = BASE_DIR / "templates" / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Smart Gateway</h1><p>面板模板未找到</p>")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "version": "1.0.0",
        "uptime": time.time() - getattr(health, "_start_time", time.time()),
    }
health._start_time = time.time()


@app.get("/metrics")
async def metrics():
    return HTMLResponse(content=generate_latest(_metrics_registry), media_type=CONTENT_TYPE_LATEST)


# ==================== 启动 ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=config["host"],
        port=config["port"],
        reload=False,
        log_level="info",
    )