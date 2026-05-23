"""Wrap Qwen-Agent's DocParser to turn a local file into text chunks.

DocParser handles .pdf/.docx/.pptx/.txt/.csv/.tsv/.xlsx/.xls/.html and
splits content into chunks (size configurable via `parser_page_size`).
Its return shape is::

    {
        'url': '/abs/path/to/file.pdf',
        'title': '...',
        'raw':   [
            {'content': '<chunk text>', 'metadata': {...}, 'token': 2534},
            ...
        ],
    }

We flatten ``raw[*].content`` to an ordered list of non-empty strings.
"""
from __future__ import annotations

import json


def parse_to_chunks(local_path: str) -> list[str]:
    from qwen_agent.tools.doc_parser import DocParser

    # `max_ref_token` is the per-chunk token ceiling DocParser uses when
    # splitting. The default (4000) produces near-single-chunk output on
    # small docs, which makes hybrid retrieval less useful. ~500 tokens
    # gives several focused chunks per page — better recall for RAG.
    parser = DocParser({"max_ref_token": 500})
    raw = parser.call({"url": local_path})
    # DocParser usually returns a dict; defensively support JSON-string too.
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            text = raw.strip()
            return [text] if text else []
    else:
        parsed = raw

    if not isinstance(parsed, dict):
        return []

    items = parsed.get("raw") or parsed.get("content") or []
    if isinstance(items, str):
        text = items.strip()
        return [text] if text else []
    if not isinstance(items, list):
        return []

    chunks: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("content") or item.get("text") or ""
        else:
            text = str(item)
        text = text.strip()
        if text:
            chunks.append(text)
    return chunks
