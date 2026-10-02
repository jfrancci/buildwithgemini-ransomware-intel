# ruff: noqa
import os
import json
import time
import urllib.request
import urllib.parse
import datetime
import uuid
from typing import Optional, List, Dict, Any
from pathlib import Path
try:
    from google.cloud import firestore
except ImportError:
    firestore = None
from google.cloud import storage
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.memory import VertexAiMemoryBankService
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.genai import types

from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
from .a2ui_utils import a2ui_callback

# Hardcoded GCP Project ID & GCS Bucket (Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT)
PROJECT_ID = "qwiklabs-gcp-02-352700260d69"
COLLECTION_NAME = "ransomware_incidents"
BUCKET_NAME = "ransomware-intel-assets-qwiklabs-gcp-02-352700260d69"
MEMORY_BANK_ID = "4519414452868612096"

def _get_firestore_db():
    return firestore.Client(project=PROJECT_ID)

def _get_code_executor() -> Optional[AgentEngineSandboxCodeExecutor]:
    """Helper to configure AgentEngineSandboxCodeExecutor using deployment_metadata.json."""
    meta_path = Path(__file__).resolve().parent.parent / "deployment_metadata.json"
    runtime_id = None
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                runtime_id = data.get("remote_agent_runtime_id")
        except Exception:
            pass

    if not runtime_id:
        runtime_id = f"projects/91774684774/locations/us-central1/reasoningEngines/7336310466672590848"

    return AgentEngineSandboxCodeExecutor(agent_engine_resource_name=runtime_id)

async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: After each agent turn, extract and send durable facts to Vertex AI Memory Bank."""
    await callback_context.add_session_to_memory()
    return None

def memory_bank_service_builder():
    """Configures the memory service for deployment using the dedicated Agent Engine Memory Bank."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )

def _make_api_pro_request(endpoint_path: str, params: Optional[dict] = None, limit: int = 20) -> str:
    """Helper function to perform robust HTTP GET requests to Ransomware.live API with smart payload slicing and retry backoff."""
    api_key = os.getenv("RANSOMWARE_LIVE_API_KEY", "96e3e582-81c6-4648-a90e-65757050cdd5")
    headers = {"User-Agent": "RansomwareIntelAgent/1.0"}
    if api_key:
        headers["X-API-KEY"] = api_key

    url = f"https://api-pro.ransomware.live{endpoint_path}"
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None}
        if clean_params:
            url = f"{url}?{urllib.parse.urlencode(clean_params)}"

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                raw_data = json.loads(response.read().decode())

                # Smart payload slicing to prevent broken JSON string truncation
                if isinstance(raw_data, dict):
                    processed = {}
                    for k, v in raw_data.items():
                        if k in ("client",):
                            continue
                        if isinstance(v, list):
                            processed[f"total_{k}_available"] = len(v)
                            processed[k] = v[:limit]
                        else:
                            processed[k] = v
                    processed["limit_applied"] = limit
                    return json.dumps(processed, indent=2)
                elif isinstance(raw_data, list):
                    processed = {
                        "total_count_available": len(raw_data),
                        "limit_applied": limit,
                        "results": raw_data[:limit]
                    }
                    return json.dumps(processed, indent=2)

                return json.dumps(raw_data, indent=2)

        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return json.dumps({"error": f"Authentication Error ({e.code}): Invalid or missing API key."})
            elif e.code == 404:
                return json.dumps({"status": "no_results", "message": "No records found for the specified query criteria."})
            elif e.code == 429 or e.code >= 500:
                if attempt < max_attempts - 1:
                    time.sleep(1 * (attempt + 1))
                    continue
                return json.dumps({"error": f"API Rate Limit or Server Error ({e.code}): {e.reason}"})
            return json.dumps({"error": f"Ransomware.live API Error ({e.code}): {e.reason}"})
        except Exception as e:
            if attempt < max_attempts - 1:
                time.sleep(1 * (attempt + 1))
                continue
            return json.dumps({"error": f"Error connecting to Ransomware.live API: {e}"})

    return json.dumps({"error": "Failed to retrieve response after max retries."})

def query_ransomware_incidents(
    group: Optional[str] = None,
    country: Optional[str] = None,
    victim: Optional[str] = None,
    sector: Optional[str] = None
) -> str:
    """Searches the internal Firestore database for ransomware incident records matching specified filters.

    Args:
        group: Optional ransomware group name to filter by (e.g. 'LockBit 3.0', 'BlackCat', 'Qilin').
        country: Optional target country name to filter by (e.g. 'United States', 'Germany').
        victim: Optional victim organization name to filter by.
        sector: Optional industry sector to filter by (e.g. 'Healthcare', 'Finance & Banking').

    Returns:
        A list of matching incident records or a message indicating no results were found.
    """
    try:
        db = _get_firestore_db()
        docs = list(db.collection(COLLECTION_NAME).stream())
        results = []

        for doc in docs:
            data = doc.to_dict()
            if group and group.lower() not in data.get("group", "").lower():
                continue
            if country and country.lower() not in data.get("country", "").lower():
                continue
            if victim and victim.lower() not in data.get("victim", "").lower():
                continue
            if sector and sector.lower() not in data.get("sector", "").lower():
                continue
            results.append(data)

        if not results:
            return "No ransomware incidents matched the query criteria."

        return f"Found {len(results)} matching incident(s):\n" + "\n".join([str(item) for item in results])
    except Exception as e:
        return f"Error querying Firestore database: {e}"

def record_ransomware_incident(
    id: str,
    group: str,
    victim: str,
    country: str,
    sector: str,
    year: int,
    month: int,
    ransom_demanded_usd: float,
    status: str,
    description: str
) -> str:
    """Adds or updates a ransomware attack incident record in the internal Firestore database.

    Args:
        id: Unique incident identifier (e.g. 'INC-2026-006').
        group: Ransomware group responsible for the attack (e.g. 'LockBit 3.0', 'Qilin').
        victim: Target organization or victim name.
        country: Target country location.
        sector: Industry sector of the victim.
        year: Year of the attack (e.g. 2026).
        month: Month number of the attack (1-12).
        ransom_demanded_usd: Demanded ransom amount in USD.
        status: Incident status (e.g. 'Active', 'Paid', 'Restored').
        description: Summary of the incident and impact.

    Returns:
        A confirmation message upon successfully saving to Firestore.
    """
    try:
        db = _get_firestore_db()
        incident_data = {
            "id": id,
            "group": group,
            "victim": victim,
            "country": country,
            "sector": sector,
            "year": year,
            "month": month,
            "ransom_demanded_usd": float(ransom_demanded_usd),
            "status": status,
            "description": description
        }
        db.collection(COLLECTION_NAME).document(id).set(incident_data)
        return f"Successfully recorded incident '{id}' for victim '{victim}' ({group}) in Firestore."
    except Exception as e:
        return f"Error writing incident to Firestore database: {e}"

def generate_threat_map(
    country: Optional[str] = None,
    group: Optional[str] = None
) -> str:
    """Generates a visual ransomware threat map image using Vertex AI Imagen and publishes it to Cloud Storage.

    Args:
        country: Optional country focus for the threat map (e.g. 'Germany', 'United States', or 'Global').
        group: Optional ransomware group focus for the threat map (e.g. 'Qilin', 'LockBit 3.0', 'BlackCat').

    Returns:
        A formatted string with the public image URL and Markdown image embed link.
    """
    try:
        target_country = country if country else "Global"
        target_group = group if group else "all threat groups"

        prompt = (
            f"A sleek high-tech cybersecurity world threat map displaying glowing red ransomware attack hotspots "
            f"and threat vectors for {target_country}, targeting {target_group}. Dark mode, neon red holographic UI, "
            f"vector graphics, high resolution threat intelligence dashboard map."
        )

        client = genai.Client(vertexai=True, project=PROJECT_ID, location="us-central1")
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt,
        )

        image_part = None
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_part = part
                    break

        if not image_part or not image_part.inline_data:
            return "Failed to generate threat map image: no image data returned from model."

        image_bytes = image_part.inline_data.data
        filename = f"threat_map_{target_country.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}.png"

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return (
            f"Threat map successfully generated and published to Cloud Storage!\n\n"
            f"**Public Image URL**: {public_url}\n\n"
            f"![Ransomware Threat Map]({public_url})"
        )
    except Exception as e:
        return f"Error generating threat map image: {e}"

async def generate_domain_threat_asset(
    prompt_description: str,
    tool_context: Optional[ToolContext] = None
) -> str:
    """Generates an image for a ransomware threat domain item (emblem, victim alert badge, threat group logo)
    using gemini-3.1-flash-lite-image in the global region. Saves the image as an ADK artifact (so it appears
    in the Playground's Artifacts panel) and uploads it directly to Cloud Storage.

    Args:
        prompt_description: Description of the ransomware threat item or incident asset to visualize (e.g. 'Qilin group emblem', 'Healthcare ransomware incident badge').

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated image.
    """
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        full_prompt = (
            f"Generate an image: High-tech dark mode cybersecurity ransomware threat intelligence graphic for: {prompt_description}. "
            f"Sleek neon red vector emblem, dark background, professional incident badge."
        )

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=full_prompt,
        )

        image_part = None
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_part = part
                    break

        if not image_part or not image_part.inline_data:
            return "Failed to generate image: No image data returned from model."

        image_bytes = image_part.inline_data.data
        filename = f"threat_asset_{uuid.uuid4().hex[:8]}.png"

        # 1. Save as ADK artifact so it shows in Playground's Artifacts panel
        if tool_context:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes directly to public Cloud Storage bucket without local file write
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating domain threat asset: {e}"

async def generate_domain_threat_video(
    prompt_description: str,
    tool_context: Optional[ToolContext] = None
) -> str:
    """Generates a short video animation for a ransomware threat domain item (e.g. ransomware attack visual, threat briefing animation, incident summary clip)
    using gemini-omni-flash-preview in the global region via the Interactions API. Saves the video as an ADK artifact (so it appears
    in the Playground's Artifacts panel) and uploads it directly to Cloud Storage without writing to a local file.

    Args:
        prompt_description: Description of the ransomware threat item or animation to visualize (e.g. 'Ransomware data encryption threat briefing video', 'Qilin attack vector animation').

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated video.
    """
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        full_prompt = (
            f"A 3 to 5 second high-tech cyber threat animation showing: {prompt_description}. "
            f"Sleek cybersecurity ransomware threat intelligence dashboard video, dark mode background."
        )

        interaction = client.interactions.create(
            model="gemini-omni-flash-preview",
            input=full_prompt
        )

        if not hasattr(interaction, "output_video") or not interaction.output_video or not getattr(interaction.output_video, "data", None):
            return "Failed to generate threat video: No video data returned from model interaction."

        raw_video_data = interaction.output_video.data
        if isinstance(raw_video_data, str):
            try:
                video_bytes = base64.b64decode(raw_video_data)
            except Exception:
                video_bytes = raw_video_data.encode("utf-8")
        else:
            video_bytes = raw_video_data

        filename = f"threat_video_{uuid.uuid4().hex[:8]}.mp4"

        # 1. Save as ADK artifact so it shows in Playground's Artifacts panel
        if tool_context:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload video bytes directly to public Cloud Storage bucket without local file write
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating domain threat video: {e}"

def fetch_group_intelligence(group_name: Optional[str] = None, limit: int = 20) -> str:
    """Fetches comprehensive intelligence about a specific ransomware group or lists all tracked groups.

    Args:
        group_name: Optional ransomware group name (e.g. 'qilin', 'lockbit3', 'blackcat'). If omitted, returns all groups.
        limit: Maximum number of group entries to return (default 20).

    Returns:
        Group intelligence including description, MITRE ATT&CK TTPs, exploited CVE vulnerabilities, tools, and leak site locations.
    """
    if group_name:
        formatted_name = urllib.parse.quote(group_name.strip().lower().replace(" ", ""))
        path = f"/groups/{formatted_name}"
    else:
        path = "/groups"
    return _make_api_pro_request(path, limit=limit)

def search_ransomware_victims(
    keyword: Optional[str] = None,
    group: Optional[str] = None,
    country: Optional[str] = None,
    sector: Optional[str] = None,
    year: Optional[str] = None,
    month: Optional[str] = None,
    limit: int = 20
) -> str:
    """Searches real-time ransomware victim disclosures across organizations, domains, sectors, countries, and dates.

    Args:
        keyword: Keyword to match against victim organization name or domain (e.g. 'hospital', 'bank').
        group: Ransomware group name (e.g. 'qilin', 'lockbit', 'blackcat').
        country: ISO 3166-1 alpha-2 country code (e.g. 'US', 'FR', 'DE').
        sector: Industry sector (e.g. 'Healthcare', 'Education').
        year: 4-digit year (e.g. '2025'). Must be combined with month if filtering by date without keyword.
        month: 2-digit month (e.g. '01', '06').
        limit: Maximum number of victim records to return (default 20).

    Returns:
        List of matching victim records enriched with domain, attack date, sector, and leak details.
    """
    if keyword:
        params = {"q": keyword, "group": group, "country": country, "sector": sector}
        return _make_api_pro_request("/victims/search", params, limit=limit)
    elif group or sector or country or (year and month):
        params = {"group": group, "country": country, "sector": sector, "year": year, "month": month}
        return _make_api_pro_request("/victims/", params, limit=limit)
    else:
        return _make_api_pro_request("/victims/recent", limit=limit)

def fetch_indicators_of_compromise(group: str, ioc_type: Optional[str] = None, limit: int = 20) -> str:
    """Retrieves technical Indicators of Compromise (IOCs) for a ransomware group.

    Args:
        group: Ransomware group name (e.g. 'qilin', 'lockbit3').
        ioc_type: Optional IOC type filter ('ip', 'md5', 'sha256', 'domain', 'email', 'btc', 'url').
        limit: Maximum number of IOC entries to return (default 20).

    Returns:
        JSON structure containing active technical indicators associated with the threat group.
    """
    formatted_group = urllib.parse.quote(group.strip().lower().replace(" ", ""))
    path = f"/iocs/{formatted_group}"
    params = {"type": ioc_type} if ioc_type else None
    return _make_api_pro_request(path, params, limit=limit)

def fetch_negotiation_logs(group: Optional[str] = None, chat_id: Optional[str] = None, limit: int = 20) -> str:
    """Retrieves leaked negotiation chat logs, initial/final ransom demands, and payment outcomes.

    Args:
        group: Optional ransomware group name (e.g. 'qilin', 'lockbit3'). If omitted, lists groups with available chat logs.
        chat_id: Optional specific chat ID obtained from listing group chats to read full message thread.
        limit: Maximum number of negotiation log entries to return (default 20).

    Returns:
        Negotiation chat threads, ransom amounts demanded/negotiated, payment status, and message history.
    """
    if group and chat_id:
        formatted_group = urllib.parse.quote(group.strip().lower().replace(" ", ""))
        formatted_chat = urllib.parse.quote(chat_id.strip())
        path = f"/negotiations/{formatted_group}/{formatted_chat}"
    elif group:
        formatted_group = urllib.parse.quote(group.strip().lower().replace(" ", ""))
        path = f"/negotiations/{formatted_group}"
    else:
        path = "/negotiations"
    return _make_api_pro_request(path, limit=limit)

def fetch_ransom_notes_or_yara(resource_type: str, group: str, item_name: Optional[str] = None, limit: int = 20) -> str:
    """Fetches raw ransom note texts or YARA detection rules associated with a ransomware group.

    Args:
        resource_type: Either 'ransomnotes' for ransom note texts, or 'yara' for YARA detection rules.
        group: Ransomware group name (e.g. 'qilin', 'lockbit3').
        item_name: Optional note filename identifier (when resource_type is 'ransomnotes').
        limit: Maximum number of items to return (default 20).

    Returns:
        Ransom note text content or YARA rule definitions for threat hunting and detection.
    """
    clean_res = resource_type.strip().lower()
    formatted_group = urllib.parse.quote(group.strip().lower().replace(" ", ""))

    if clean_res == "ransomnotes":
        if item_name:
            formatted_item = urllib.parse.quote(item_name.strip())
            path = f"/ransomnotes/{formatted_group}/{formatted_item}"
        else:
            path = f"/ransomnotes/{formatted_group}"
    elif clean_res == "yara":
        path = f"/yara/{formatted_group}"
    else:
        return "Invalid resource_type. Must be either 'ransomnotes' or 'yara'."

    return _make_api_pro_request(path, limit=limit)

def fetch_sec_8k_cyber_disclosures(
    ticker: Optional[str] = None,
    year: Optional[str] = None,
    month: Optional[str] = None,
    limit: int = 20
) -> str:
    """Retrieves US SEC Form 8-K filings for material cybersecurity incidents (Item 1.05 and Item 8.01).

    Args:
        ticker: Optional stock ticker symbol (e.g. 'MSFT', 'CRM').
        year: Optional 4-digit filing year (e.g. '2025').
        month: Optional 2-digit filing month (e.g. '01').
        limit: Maximum number of filing records to return (default 20).

    Returns:
        SEC filings related to material cybersecurity disclosures filed by public companies.
    """
    params = {"ticker": ticker, "year": year, "month": month}
    return _make_api_pro_request("/8k", params, limit=limit)

def fetch_csirt_cert_contacts(country: str, limit: int = 20) -> str:
    """Retrieves official CSIRT (Computer Security Incident Response Team) and CERT contact points by country.

    Args:
        country: ISO 2-letter (e.g. 'US', 'FR', 'DE') or 3-letter (e.g. 'USA', 'FRA') country code.
        limit: Maximum number of contact entries to return (default 20).

    Returns:
        Emergency incident response team contacts, emergency phone numbers, and ENISA/FIRST registry data.
    """
    formatted_country = urllib.parse.quote(country.strip().upper())
    path = f"/csirt/{formatted_country}"
    return _make_api_pro_request(path, limit=limit)

def fetch_global_threat_stats() -> str:
    """Retrieves high-level global ransomware statistics and victim counts broken down by industry sector.

    Returns:
        Total victim counts, active groups, press entries, last update timestamp, and sector breakdown.
    """
    stats_data = _make_api_pro_request("/stats", limit=50)
    sectors_data = _make_api_pro_request("/listsectors", limit=50)
    return f"=== GLOBAL STATS ===\n{stats_data}\n\n=== SECTOR BREAKDOWN ===\n{sectors_data}"

schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Ransomware Intel Agent, an advanced cybersecurity threat intelligence AI assistant. "
        "You possess rich real-time access to the Ransomware.live API, internal Firestore databases, "
        "Vertex AI image generation capabilities, cross-session Vertex AI Memory Bank, and a secure Agent Engine Python sandbox. "
        "NOTE: All attacks conducted by the Qilin ransomware group are recognized as the most sophisticated, extremely difficult to recover from, and exceptionally hard to negotiate."
    ),
    workflow_description="Analyze the threat intelligence request, invoke tools when needed, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=_get_code_executor(),
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    tools=[
        PreloadMemoryTool(),
        generate_domain_threat_asset,
        generate_domain_threat_video,
        query_ransomware_incidents,
        record_ransomware_incident,
        generate_threat_map,
        fetch_group_intelligence,
        search_ransomware_victims,
        fetch_indicators_of_compromise,
        fetch_negotiation_logs,
        fetch_ransom_notes_or_yara,
        fetch_sec_8k_cyber_disclosures,
        fetch_csirt_cert_contacts,
        fetch_global_threat_stats,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
