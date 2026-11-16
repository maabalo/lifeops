"""
LifeOps — a local model (Ollama on this computer) as the document reader. Free and private: the document never
leaves the machine. Off unless Ollama is running here with a model AND the user chooses it in Settings.

Same safety rules as the Claude reader (app/claude_reader.py), whose instructions and answer form it reuses:
- The document is DATA, never instructions: it goes between markers, the system prompt says so, the model has no
  tools, and its only way to answer is the strict JSON form (Ollama's `format`). validate_proposal() checks the
  answer again before anyone sees it.
- Only an address on this computer is accepted (LIFEOPS_OLLAMA_URL), so a setting can't make LifeOps send
  documents or requests anywhere else.
- Photos go in as images (metadata already removed; HEIC converted first). PDFs go in as their text, because
  Ollama can't open PDFs: a scanned PDF with no text is refused with a plain reason.

Settings (environment): LIFEOPS_OLLAMA_URL (default http://127.0.0.1:11434), LIFEOPS_OLLAMA_MODEL (default: the
first model Ollama has; pick a vision model such as gemma3:12b to read photos).
Standard library only (urllib): no extra package.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .claude_reader import OUTPUT_SCHEMA, SYSTEM_PROMPT, ReaderError, Source, b64
from .extractor import SCHEMA_VERSION, validate_proposal

DEFAULT_URL = "http://127.0.0.1:11434"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
CHECK_TIMEOUT = 1.5  # seconds: is Ollama running? (asked when Settings opens)
READ_TIMEOUT = 300  # seconds: a local model on a laptop can take a while
MAX_TEXT_CHARS = 60_000  # long PDFs are cut, so a small model's memory isn't overrun


def base_url() -> str | None:
    """Ollama's address, only if it's on this computer (else None: the local reader is unavailable)."""
    url = os.environ.get("LIFEOPS_OLLAMA_URL", DEFAULT_URL).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or parts.path or parts.query:
        return None
    return url


def call(path: str, body: dict | None, timeout: float) -> dict:
    """One request to Ollama; JSON in, JSON out."""
    url = base_url()
    if url is None:
        raise ReaderError("LIFEOPS_OLLAMA_URL must be an http:// address on this computer.")
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{url}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (address checked above)
        return json.loads(response.read())


def installed_models() -> list[str]:
    try:
        tags = call("/api/tags", None, CHECK_TIMEOUT)
    except (ReaderError, OSError, ValueError):
        return []
    return [m["name"] for m in tags.get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str)]


def local_model() -> str | None:
    """The model to use: LIFEOPS_OLLAMA_MODEL if Ollama has it, else the first one Ollama has, else None."""
    models = installed_models()
    wanted = os.environ.get("LIFEOPS_OLLAMA_MODEL", "").strip()
    if wanted:
        return next((m for m in models if m == wanted or m == f"{wanted}:latest"), None)
    return models[0] if models else None


def build_request(model: str, source: Source) -> dict:
    """The /api/chat body: instructions, then the document between markers (text, or a photo as an image)."""
    message: dict = {"role": "user"}
    if source.data is not None and source.media_type in ("image/jpeg", "image/png"):
        text = "(The document is the attached photo.)"
        message["images"] = [b64(source.data)]
    else:
        pages = [p for p in source.pages if p.strip()]
        if not pages:
            raise ReaderError("This file has no text the local model can read (scanned PDF). Use Claude or fill it in by hand.")
        text = "\n\n".join(f"[Page {n}]\n{p}" for n, p in enumerate(source.pages, start=1) if p.strip())[:MAX_TEXT_CHARS]
    message["content"] = f"<document>\n{text}\n</document>\nFill in the form for the document above. Answer with the JSON form only."
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, message],
        "format": OUTPUT_SCHEMA,  # Ollama only lets the model answer in this exact shape
        "stream": False,
        "options": {"temperature": 0},  # the same document should give the same answer
    }


class LocalExtractor:
    """Reads one document with the local model. Tests replace `make_local` in routers/documents.py."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.name = f"ollama:{model}"

    def extract(self, source: Source) -> dict:
        body = build_request(self.model, source)
        try:
            answer = call("/api/chat", body, READ_TIMEOUT)
        except TimeoutError:
            raise ReaderError("The local model took too long. Try again, or use a smaller model.") from None
        except urllib.error.HTTPError as error:
            raise ReaderError(f"Ollama couldn't read this document ({error.code}). Is the model a vision model?") from None
        except (urllib.error.URLError, OSError):
            raise ReaderError("Couldn't reach Ollama. Open the Ollama app, then try again.") from None
        except ValueError:
            raise ReaderError("Ollama's answer wasn't in the expected format. Try again.") from None
        if answer.get("done_reason") == "length":
            raise ReaderError("The local model's answer was cut off. Try again.")
        try:
            fields = json.loads(answer["message"]["content"])
        except (KeyError, TypeError, ValueError):
            raise ReaderError("The local model's answer wasn't in the expected format. Try again.") from None
        return validate_proposal({"schema": SCHEMA_VERSION, "fields": fields})
