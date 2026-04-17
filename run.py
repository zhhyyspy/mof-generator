"""Entry point: launch the FastAPI server."""
import uvicorn
from backend.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=["backend", "frontend"],
        reload_excludes=["backend/data/*", "*.json", "*.meta.json"],  # Don't watch data files
    )
