import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat as chat_router
from app.api import chat_sessions as chat_sessions_router
from app.api import documents as documents_router
from app.api import landing_config as landing_config_router
from app.api import system_config as system_config_router
from app.api import users as users_router
from app.core.config import get_settings

# Surface our own loggers (app.perf, app.services.*) through uvicorn's stdout.
# Uvicorn only opts its own namespace into INFO by default.
logging.getLogger("app").setLevel(logging.INFO)
if not logging.getLogger("app").handlers:
    logging.getLogger("app").addHandler(logging.StreamHandler())

settings = get_settings()

app = FastAPI(title="Knowledgebase Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents_router.router)
app.include_router(chat_router.router)
app.include_router(chat_sessions_router.router)
app.include_router(landing_config_router.router)
app.include_router(system_config_router.router)
app.include_router(users_router.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"data": "Knowledgebase Chatbot API"}
