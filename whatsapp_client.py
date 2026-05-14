"""
whatsapp_client.py
------------------
Thin async wrapper around the WhatsApp Cloud API (Meta Graph API).

Provides:
  - send_text(to, body)
  - send_image(to, media_id, caption=None)
  - send_document(to, media_id, filename, caption=None)
  - upload_media(file_bytes, mime_type, filename) → media_id
  - download_media(media_id) → bytes
  - mark_as_read(message_id)
  - verify_signature(body, signature) → bool

All functions are async and raise httpx.HTTPStatusError on non-2xx responses
unless caught internally.

Authentication: every call uses WHATSAPP_ACCESS_TOKEN as a Bearer token.
Endpoints are scoped under WHATSAPP_PHONE_NUMBER_ID for the sender side and
under the media ID for the receive side.
"""

import asyncio
import hashlib
import hmac
import logging
from typing import Any

import httpx

from config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_GRAPH_VERSION,
)

logger = logging.getLogger(__name__)

_GRAPH_BASE = f"https://graph.facebook.com/{WHATSAPP_GRAPH_VERSION}"
_AUTH_HEADERS = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
_JSON_HEADERS = {**_AUTH_HEADERS, "Content-Type": "application/json"}

# Module-level client — connection pooling + HTTP/2 reuse
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


# ---------------------------------------------------------------------------
# Outbound — sending messages
# ---------------------------------------------------------------------------

async def send_text(to: str, body: str) -> None:
    """
    Send a plain text message. WhatsApp accepts up to 4096 chars per message.
    Caller is responsible for splitting longer text.
    """
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }
    await _post_messages(payload, what="text")


async def send_image(to: str, media_id: str, caption: str | None = None) -> None:
    """Send an image previously uploaded via upload_media()."""
    image_block: dict[str, Any] = {"id": media_id}
    if caption:
        image_block["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image_block,
    }
    await _post_messages(payload, what="image")


async def send_document(
    to: str, media_id: str, filename: str, caption: str | None = None
) -> None:
    """Send a document (Excel, PowerPoint, PDF, etc.) by media ID."""
    doc_block: dict[str, Any] = {"id": media_id, "filename": filename}
    if caption:
        doc_block["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": doc_block,
    }
    await _post_messages(payload, what="document")


async def mark_as_read(message_id: str) -> None:
    """
    Mark an inbound message as read (shows the blue ticks to the user).
    Best-effort — failures are logged but don't propagate.
    """
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        await _post_messages(payload, what="read-receipt")
    except Exception as exc:
        logger.debug(f"mark_as_read failed (ignored): {exc}")


_RETRY_STATUSES = {429, 500, 502, 503}


async def _post_messages(payload: dict, what: str) -> dict:
    url = f"{_GRAPH_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    client = _get_client()
    for attempt in range(3):
        resp = await client.post(url, json=payload, headers=_JSON_HEADERS)
        if resp.status_code in _RETRY_STATUSES and attempt < 2:
            wait = 2 ** attempt
            logger.warning(
                f"WhatsApp {what} send: HTTP {resp.status_code}, "
                f"retry in {wait}s (attempt {attempt + 1}/3)"
            )
            await asyncio.sleep(wait)
            continue
        if resp.status_code >= 400:
            logger.error(
                f"WhatsApp {what} send failed: HTTP {resp.status_code} — {resp.text}"
            )
            resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()  # exhausted retries


# ---------------------------------------------------------------------------
# Outbound — uploading binary media (image, document) for later send
# ---------------------------------------------------------------------------

async def upload_media(file_bytes: bytes, mime_type: str, filename: str) -> str:
    """
    Upload a file to Meta and return the media ID. Re-use the ID with
    send_image / send_document. Media IDs expire after ~30 days; for our
    use-case (immediate send) that's plenty.
    """
    url = f"{_GRAPH_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/media"
    files = {
        "file": (filename, file_bytes, mime_type),
        "messaging_product": (None, "whatsapp"),
        "type": (None, mime_type),
    }
    client = _get_client()
    for attempt in range(3):
        resp = await client.post(url, files=files, headers=_AUTH_HEADERS)
        if resp.status_code in _RETRY_STATUSES and attempt < 2:
            wait = 2 ** attempt
            logger.warning(
                f"WhatsApp media upload: HTTP {resp.status_code}, "
                f"retry in {wait}s (attempt {attempt + 1}/3)"
            )
            await asyncio.sleep(wait)
            continue
        if resp.status_code >= 400:
            logger.error(
                f"WhatsApp media upload failed: HTTP {resp.status_code} — {resp.text}"
            )
            resp.raise_for_status()
        break
    media_id = resp.json().get("id")
    if not media_id:
        raise RuntimeError(f"upload_media returned no id: {resp.json()}")
    return media_id


# ---------------------------------------------------------------------------
# Inbound — downloading media a user sent us (voice, image, etc.)
# ---------------------------------------------------------------------------

async def download_media(media_id: str) -> bytes:
    """
    Two-step download: GET media metadata to fetch a signed URL, then GET the
    URL to retrieve the bytes. Both steps use the access token.
    """
    client = _get_client()

    meta_url = f"{_GRAPH_BASE}/{media_id}"
    meta_resp = await client.get(meta_url, headers=_AUTH_HEADERS)
    meta_resp.raise_for_status()
    media_url = meta_resp.json().get("url")
    if not media_url:
        raise RuntimeError(f"download_media: no url in metadata: {meta_resp.json()}")

    media_resp = await client.get(media_url, headers=_AUTH_HEADERS)
    media_resp.raise_for_status()
    return media_resp.content


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, signature_header: str | None) -> bool:
    """
    Validate the X-Hub-Signature-256 header sent by Meta with each webhook.
    Returns True if the body is signed with WHATSAPP_APP_SECRET.

    If WHATSAPP_APP_SECRET is unset, returns True (allows local testing
    without the secret — but you SHOULD set it in production).
    """
    if not WHATSAPP_APP_SECRET:
        return True
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header.split("=", 1)[1]
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, expected)


# ---------------------------------------------------------------------------
# Cleanup (call on app shutdown)
# ---------------------------------------------------------------------------

async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
