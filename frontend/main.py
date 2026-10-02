"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.

Why A2A: agents-cli 1.1.0 (GA) deploys ADK agents to Agent Runtime as A2A agents
and no longer registers the reasoning-engine operation schema the old
`agent_engines.get(...).stream_query()` path relied on (operation_schemas() comes
back empty). The container serves the A2A protocol over the Agent Engine HTTP
passthrough, so this proxy fetches the agent's card and sends messages with the
a2a-sdk client (the same path `agents-cli run --mode a2a` uses). This works for
both A2A and plain ADK 1.1.0 deployments (the container serves A2A either way).

Run:
  pip install -r requirements.txt
  export AGENT_ENGINE_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."
  export AGENT_DIRECTORY="app"   # your agent's app directory (agents-cli-manifest.yaml)
  python main.py                 # -> http://localhost:8080
"""

import base64
import json
import os
import re
import uuid
from pathlib import Path

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    FilePart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TextPart,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

RESOURCE = os.environ["AGENT_ENGINE_RESOURCE_NAME"]
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

# One set of ADC credentials, refreshed per request (access tokens expire ~1h).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    # Always return JSON so the browser never receives a plain-text 500 page
    # (which shows up in the chat as "Unexpected token 'I', "Internal S"... is
    # not valid JSON"). Any server-side failure now surfaces as a readable
    # message in the chat bubble instead.
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        card = AgentCard(**resp.json())
        # Agent Runtime does not serve a public card URL, so point the client at
        # the passthrough base for message sends.
        card.url = A2A_BASE
        _card = card
    return _card


def _parse_raw_text(text: str) -> list[dict]:
    out: list[dict] = []
    pattern = r"<(a2ui-json|a2a_datapart_json)>(.*?)</?\1>"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        clean_text = re.sub(r"<(?:a2ui-json|a2a_datapart_json)>.*?</?(?:a2ui-json|a2a_datapart_json)>", "", text, flags=re.DOTALL).strip()
        if clean_text and not clean_text.startswith("Cannot add session to memory"):
            out.append({"kind": "text", "text": clean_text})
        for tag, content in matches:
            try:
                data_obj = json.loads(content.strip())
                if isinstance(data_obj, list):
                    for item in data_obj:
                        out.append({"kind": "a2ui", "data": item})
                elif isinstance(data_obj, dict):
                    data_payload = data_obj.get("data") or data_obj
                    out.append({"kind": "a2ui", "data": data_payload})
            except Exception:
                pass
    else:
        clean_text = text.strip()
        if clean_text and not clean_text.startswith("Cannot add session to memory"):
            out.append({"kind": "text", "text": clean_text})
    return out


def _get_field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _extract_parts(parts: list) -> list[dict]:
    """Turn A2A response parts into structured parts for the chat UI."""
    out: list[dict] = []
    for p in parts:
        root = _get_field(p, "root") or p
        
        # Check text field
        text = _get_field(root, "text")
        if text:
            out.extend(_parse_raw_text(str(text)))
            continue

        # Check data field
        data = _get_field(root, "data")
        if data is not None:
            meta = _get_field(root, "metadata") or {}
            mime = meta.get("mimeType") if isinstance(meta, dict) else _get_field(meta, "mimeType")
            if mime == _A2UI_MIME or (isinstance(data, dict) and ("surfaceUpdate" in data or "beginRendering" in data)):
                out.append({"kind": "a2ui", "data": data})
                continue

        # Check file field
        f = _get_field(root, "file")
        if f:
            b = _get_field(f, "bytes")
            uri = _get_field(f, "uri")
            if b:
                try:
                    if isinstance(b, bytes):
                        raw_str = b.decode("utf-8")
                    else:
                        try:
                            raw_str = base64.b64decode(b).decode("utf-8")
                        except Exception:
                            raw_str = str(b)
                    out.extend(_parse_raw_text(raw_str))
                except Exception:
                    pass
            elif uri:
                out.append({"kind": "text", "text": str(uri)})
    return out


def _extract_parts_from_history(history: list) -> list[dict]:
    out: list[dict] = []
    for msg in history:
        role = _get_field(msg, "role")
        role_str = str(role or "").lower()
        if role_str and "user" not in role_str:
            parts = _get_field(msg, "parts") or []
            out.extend(_extract_parts(parts))
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "web-user"
    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        factory = ClientFactory(
            ClientConfig(
                supported_transports=[
                    TransportProtocol.jsonrpc,
                    TransportProtocol.http_json,
                ],
                httpx_client=client,
            )
        )
        a2a_client = factory.create(card)

        msg = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(text=message))],
            context_id=_contexts.get(user_id),
        )

        last_task = None
        got_artifact_update = False
        async for event in a2a_client.send_message(msg):
            if isinstance(event, tuple):
                task, update = event
            else:
                task, update = event, None

            if task is not None:
                last_task = task
                if getattr(task, "context_id", None):
                    _contexts[user_id] = task.context_id
                if getattr(task, "history", None):
                    new_parts = _extract_parts_from_history(task.history)
                    for np in new_parts:
                        if np not in parts:
                            parts.append(np)

            if isinstance(update, TaskArtifactUpdateEvent):
                got_artifact_update = True
                parts.extend(_extract_parts(update.artifact.parts))

        # Non-streaming fallback: pull parts from artifacts or history.
        if not parts and last_task is not None:
            for artifact in getattr(last_task, "artifacts", None) or []:
                parts.extend(_extract_parts(artifact.parts))
            if not parts and getattr(last_task, "history", None):
                parts.extend(_extract_parts_from_history(last_task.history))

    if not parts:
        # The turn produced no text or UI (e.g. the agent only ran tools, or a
        # tool stalled). Be honest rather than silent.
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]
    return JSONResponse({"parts": parts})


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
