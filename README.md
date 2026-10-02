# Ransomware Intel Agent

**Ransomware Intel Agent** is an AI-powered threat intelligence assistant designed for security analysts and incident responders. It provides real-time tracking of ransomware groups, target victim profiles, attack metrics, indicators of compromise (IOCs), ransom notes, YARA rules, and SEC 8-K cyber disclosures.

---

## Key Capabilities & Features

### 🔍 Real-Time Threat Intelligence
- **Ransomware.live API Integration**: Queries active ransomware groups, victim logs, leak sites, MITRE ATT&CK TTPs, and exploited CVE vulnerabilities.
- **Indicators of Compromise (IOCs)**: Retrieves malicious IP addresses, file hashes (MD5, SHA256), FTP credentials, and domain IOCs associated with threat groups.
- **Ransom Notes & Negotiation Logs**: Accesses actual ransom note samples, YARA detection rules, and negotiated ransom chat transcripts.
- **SEC 8-K Disclosures & CSIRT Contacts**: Fetches SEC 8-K cybersecurity breach filings for public victim companies and national emergency response team (CSIRT/CERT) contact details.

### 🌩️ Integrated Google Cloud Services
- **Google Cloud Firestore**: Persists and queries internal ransomware attack incident records (`query_ransomware_incidents`, `record_ransomware_incident`).
- **Google Cloud Storage (GCS)**: Stores generated threat map images, emblems, and MP4 video animations with public HTTPS access.
- **Vertex AI Memory Bank**: Retains cross-session memory of analyst focus areas, target groups, and session history (`VertexAiMemoryBankService` & `PreloadMemoryTool`).
- **Vertex AI Imagen & Gemini Omni**:
  - `gemini-2.5-flash-image`: Generates high-resolution global ransomware threat map images.
  - `gemini-3.1-flash-lite-image` (global region): Generates domain threat emblems, victim alert badges, and saves them as ADK artifacts.
  - `gemini-omni-flash-preview` (global region via Interactions API): Generates 3–5 second cyber threat animation videos (`.mp4`), saves them as ADK artifacts, and uploads them directly to GCS without writing local files.
- **Agent Engine Sandbox Code Executor**: Runs secure Python code in a sandboxed environment to calculate attack KPIs, statistical trends, and victim distributions.

### 🎨 Rich User Interface & A2UI Rendering
- **A2UI v0.8 Basic Catalog**: Renders agent responses as structured UI cards (Card, Column, Row, Text, Image) in both the ADK Web Playground and custom web frontends.
- **Custom Crimson Threat Intelligence Chat UI**: A responsive web frontend featuring:
  - Custom dark/crimson theme tailored for cybersecurity analysts.
  - Quick-start prompt chips for common queries.
  - Sender badges (`ANALYST` vs `RANSOMWARE INTEL`).
  - Animated threat intelligence loading status.
  - Client-side A2UI renderer for card components.

---

## Project Architecture

```
ransomware-intel/
├── app/
│   ├── agent.py               # Root agent, tools, Firestore, Vertex AI Memory Bank, and GenAI models
│   ├── a2ui_utils.py          # A2UI callback transformer for Agent Engine & ADK
│   ├── fast_api_app.py        # ADK FastAPI backend service
│   └── app_utils/             # Utility helpers
├── frontend/
│   ├── main.py                # FastAPI proxy connecting browser to deployed agent over A2A protocol
│   └── static/
│       └── index.html         # Single-page chat interface with A2UI renderer
├── agents-cli-manifest.yaml   # ADK / Agents CLI deployment manifest
├── deployment_metadata.json   # Deployed Reasoning Engine resource metadata
└── pyproject.toml             # Python dependencies and build configuration
```

---

## Tools Reference

| Tool Name | Cloud Service / Backend | Description |
| --- | --- | --- |
| `PreloadMemoryTool` | Vertex AI Memory Bank | Preloads cross-session analyst facts and preferences. |
| `query_ransomware_incidents` | Google Cloud Firestore | Queries internal incident records by group, victim, country, or sector. |
| `record_ransomware_incident` | Google Cloud Firestore | Saves new ransomware incident data to Firestore. |
| `generate_threat_map` | Vertex AI Imagen & GCS | Generates visual threat maps (`gemini-2.5-flash-image`). |
| `generate_domain_threat_asset` | Vertex AI Imagen, GCS & ADK Artifacts | Generates threat emblems (`gemini-3.1-flash-lite-image`). |
| `generate_domain_threat_video` | Gemini Omni, GCS & ADK Artifacts | Generates short threat videos (`gemini-omni-flash-preview`). |
| `fetch_group_intelligence` | Ransomware.live API | Fetches group profiles, MITRE TTPs, CVEs, and leak sites. |
| `search_ransomware_victims` | Ransomware.live API | Searches victim logs across sectors and countries. |
| `fetch_indicators_of_compromise` | Ransomware.live API | Retrieves IP, hash, and domain IOCs for threat groups. |
| `fetch_negotiation_logs` | Ransomware.live API | Retrieves ransom negotiation chat transcripts and ransom demands. |
| `fetch_ransom_notes_or_yara` | Ransomware.live API | Retrieves sample ransom notes and YARA detection rules. |
| `fetch_sec_8k_cyber_disclosures` | Ransomware.live API | Searches SEC 8-K cyber breach filings for public companies. |
| `fetch_csirt_cert_contacts` | Ransomware.live API | Returns national CSIRT/CERT emergency response contacts. |
| `fetch_global_threat_stats` | Ransomware.live API | Retrieves global attack totals and sector breakdown metrics. |

---

## Local Setup & Development

### 1. Prerequisites
- Python 3.11+
- `uv` (Python package manager)
- `google-agents-cli` (`1.1.0`)

### 2. Environment Setup
Install project dependencies:

```bash
agents-cli install
```

Set required environment variables:

```bash
export RANSOMWARE_LIVE_API_KEY="<your-api-key>"
```

### 3. Running in ADK Playground
To launch the agent locally in the ADK Web Playground:

```bash
agents-cli playground
```

### 4. Running the Frontend Proxy Server
To run the custom FastAPI chat frontend locally:

```bash
cd frontend
export AGENT_ENGINE_RESOURCE_NAME="<your-deployed-reasoning-engine-resource-name>"
export AGENT_DIRECTORY="app"
export PORT=8080

python3 main.py
```

The frontend server starts on port `8080` and proxies chat requests via A2A protocol to your deployed Agent Engine reasoning engine.
