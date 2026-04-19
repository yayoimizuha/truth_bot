from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from .schemas import MediaAttachment, NormalizedPost

MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]+)")
URL_RE = re.compile(r"https?://[^\s]+")
TRUTH_HANDLE_RE = re.compile(r"/@([A-Za-z0-9_.-]+)")
LEADING_MENTION_RE = re.compile(r"^@([A-Za-z0-9_.-]+)\s*")


def _render_node(node: Any) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    if node.name == "br":
        return "\n"
    if node.name in {"p", "div", "li"}:
        inner = "".join(_render_node(child) for child in node.children)
        return f"{inner}\n"
    return "".join(_render_node(child) for child in node.children)


def _split_leading_mentions(text: str) -> tuple[list[str], str]:
    mentions: list[str] = []
    remaining = text
    while True:
        match = LEADING_MENTION_RE.match(remaining)
        if match is None:
            return mentions, remaining.strip()
        mentions.append(match.group(1))
        remaining = remaining[match.end():]


def normalize_text(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html or "", "lxml")
    urls: list[str] = []

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href:
            urls.append(href)
        handle_match = TRUTH_HANDLE_RE.search(href or "")
        if handle_match and not anchor.get_text(strip=True).startswith("@"):
            anchor.string = f"@{handle_match.group(1)}"

    raw = "".join(_render_node(child) for child in soup.children)
    raw = raw.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
    plain_text = "\n".join(line for line in lines if line).strip()

    expanded_urls = list(dict.fromkeys(urls + URL_RE.findall(plain_text)))
    return plain_text, expanded_urls


def normalize_status(payload: dict[str, Any]) -> NormalizedPost:
    plain_text, expanded_urls = normalize_text(payload.get("content") or "")
    leading_mentions, command_text = _split_leading_mentions(plain_text)
    inline_mentions = [m.group(1) for m in MENTION_RE.finditer(command_text)]
    llm_text = command_text

    media = [
        MediaAttachment(
            media_id=str(item["id"]),
            media_type=item.get("type") or "unknown",
            url=item.get("url") or item.get("preview_url") or "",
            preview_url=item.get("preview_url"),
            mime_type=item.get("mime_type"),
        )
        for item in payload.get("media_attachments", [])
    ]

    return NormalizedPost(
        post_id=str(payload["id"]),
        author_handle=payload["account"]["acct"],
        author_display_name=payload["account"].get("display_name") or payload["account"]["acct"],
        parent_post_id=str(payload["in_reply_to_id"]) if payload.get("in_reply_to_id") else None,
        raw_content=payload.get("content") or "",
        plain_text=plain_text,
        llm_text=llm_text,
        command_text=command_text,
        leading_mentions=leading_mentions,
        inline_mentions=inline_mentions,
        expanded_urls=expanded_urls,
        media=media,
        created_at=payload.get("created_at"),
        quote_post_id=str(payload["quote_id"]) if payload.get("quote_id") else None,
        source_payload=payload,
    )
