from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Keitaro Wrapper", description="Обёртка над трекером Keitaro")
app.include_router(router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
