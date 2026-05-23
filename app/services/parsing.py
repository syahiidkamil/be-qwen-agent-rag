"""Wrap Qwen-Agent's DocParser to turn a local file into text chunks.

DocParser handles .pdf/.docx/.pptx/.txt/.csv/.tsv/.xlsx/.xls/.html and
splits content into ~500-token chunks (configurable via `parser_page_size`).
We re-export a single function the ingestion pipeline calls.
"""
from __future__ import annotations


def parse_to_chunks(local_path: str) -> list[str]:
    """Return text chunks from a parseable document.

    DocParser returns a structured result; we flatten it to a flat list of
    chunk strings ordered by appearance.
    """
    # Imported lazily to keep startup fast and avoid loading the heavy
    # qwen-agent deps for unrelated endpoints.
    from qwen_agent.tools.doc_parser import DocParser

    parser = DocParser()
    raw = parser.call({"url": local_path})
    # `raw` is a JSON string of {title, content: [chunk, ...]} or similar.
    import json
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        # Fallback: treat raw as a single chunk
        return [str(raw)]

    chunks: list[str] = []
    content = parsed.get("content") if isinstance(parsed, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                # DocParser items look like {"page": int, "text": str} or similar
                text = item.get("text") or item.get("content") or ""
            else:
                text = str(item)
            text = text.strip()
            if text:
                chunks.append(text)
    elif isinstance(content, str):
        chunks.append(content.strip())
    return chunks
