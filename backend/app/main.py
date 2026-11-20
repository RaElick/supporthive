import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agents.llm import get_llm
from .api import admin, operator, widget, ws
from .config import get_settings
from .db import init_db, session_scope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supporthive")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .seed import seed_if_empty

    with session_scope() as s:
        seed_if_empty(s)
    log.info("LLM mode: %s", "enabled" if get_llm() else "heuristic (LLM_BASE_URL not set)")
    bot = None
    settings = get_settings()
    if settings.telegram_bot_token:
        from .telegram_bot import TelegramBot

        bot = TelegramBot(settings.telegram_bot_token)
        bot.start()
    yield
    if bot:
        await bot.stop()


app = FastAPI(title="SupportHive", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(widget.router)
app.include_router(operator.router)
app.include_router(admin.router)
app.include_router(ws.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "llm": get_llm() is not None}


# Serve the built frontend (npm run build) so one process is enough for a demo.
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        f = FRONTEND_DIST / path
        if path and f.is_file() and FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
