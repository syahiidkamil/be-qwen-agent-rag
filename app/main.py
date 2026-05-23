from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat as chat_router
from app.api import documents as documents_router
from app.api import landing_config as landing_config_router
from app.core.config import get_settings

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
app.include_router(landing_config_router.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"data": "Knowledgebase Chatbot API"}
