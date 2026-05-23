"""DashScope embeddings via the OpenAI-compatible endpoint.

We use httpx directly rather than openai-py to keep the dependency surface
small (openai is already a transitive dep of qwen-agent but we don't lean
on it for our own code).
"""
from __future__ import annotations

import asyncio

import httpx

from app.core.config import get_settings

# DashScope caps batch size at 25 inputs per call (compatible-mode).
BATCH_SIZE = 25


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts. Order of returned vectors matches input order."""
    if not texts:
        return []

    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set; cannot embed.")

    url = settings.dashscope_base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }

    results: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            payload = {"model": settings.qwen_embedding_model, "input": batch}
            # Small retry loop for transient errors
            for attempt in range(3):
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    break
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
            data = resp.json()
            for item in data["data"]:
                results.append(item["embedding"])
    return results
