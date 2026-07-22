import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.case5_routes import router as case5_router
from api.case4_routes import router as case4_router
from api.chat import router as chat_router
from api.graph_route import router as graph_router
from api.pdf_chat import router as pdf_router, start_cleanup_task
from model.config import validate_required_config

# ── 全局日志配置 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期钩子。
    启动时：验证必需的环境变量，缺失则拒绝启动。
    关闭时：记录日志（可在此释放资源）。
    """
    logger.info("服务启动中，正在验证配置...")
    validate_required_config()
    asyncio.create_task(start_cleanup_task())
    logger.info("配置验证通过，服务已就绪 → http://localhost:8000")
    yield
    logger.info("服务已关闭")


app = FastAPI(title="eBRAM AI 文档助手", lifespan=lifespan)

# ── 业务路由 ──────────────────────────────────────────────────────────────────
app.include_router(chat_router)
app.include_router(graph_router)
app.include_router(pdf_router)
app.include_router(case4_router)
app.include_router(case5_router)

# ── 静态文件（前端资源）──────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    """根路由：返回场景选择页。"""
    return FileResponse("static/index.html")


@app.get("/case1")
async def case1():
    """Case 1：双方争议分析页面。"""
    return FileResponse("static/case1.html")


@app.get("/case2")
async def case2():
    """Case 2：调解员简报页面（前端占位，后端开发中）。"""
    return FileResponse("static/case2.html")


@app.get("/case5")
async def case5():
    """Case 5：HKLII 案例检索页面。"""
    return FileResponse("static/case5.html")


@app.get("/case4")
async def case4():
    """Case 4：英繁双向 PDF 翻译页面。"""
    return FileResponse("static/case4.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
