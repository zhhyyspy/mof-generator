"""FastAPI application factory."""
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import documents, m2_templates, models, extraction, export, llm_config


def create_app() -> FastAPI:
    app = FastAPI(
        title="MOF M1 Model Generator",
        description="AI辅助M1建模工具 — 基于MOF元模型体系",
        version="1.0.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler — prevents server crash on unhandled errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        tb = traceback.format_exc()
        print(f"[ERROR] {request.method} {request.url.path}: {exc}\n{tb}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"服务器内部错误: {str(exc)[:500]}"},
        )

    # API routers
    app.include_router(documents.router)
    app.include_router(m2_templates.router)
    app.include_router(models.router)
    app.include_router(extraction.router)
    app.include_router(export.router)
    app.include_router(llm_config.router)

    # Serve frontend static files
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()
