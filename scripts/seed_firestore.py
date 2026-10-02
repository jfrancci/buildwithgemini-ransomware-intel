import logging
from google.cloud import firestore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Hardcoded GCP Project ID (Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT)
PROJECT_ID = "qwiklabs-gcp-02-352700260d69"
COLLECTION_NAME = "ransomware_incidents"

SEED_INCIDENTS = [
    {
        "id": "INC-2026-001",
        "group": "LockBit 3.0",
        "victim": "AeroTech Global",
        "country": "United States",
        "sector": "Aerospace & Defense",
        "year": 2026,
        "month": 8,
        "ransom_demanded_usd": 5000000.0,
        "status": "Active",
        "description": "Exfiltrated 200GB of proprietary CAD drawings and encrypted core production servers."
    },
    {
        "id": "INC-2026-002",
        "group": "BlackCat",
        "victim": "HealthFirst Care Systems",
        "country": "United Kingdom",
        "sector": "Healthcare",
        "year": 2026,
        "month": 9,
        "ransom_demanded_usd": 3500000.0,
        "status": "Restored",
        "description": "Disrupted patient scheduling databases across 12 regional clinic locations."
    },
    {
        "id": "INC-2026-003",
        "group": "Play",
        "victim": "FinTrans Europe",
        "country": "Germany",
        "sector": "Finance & Banking",
        "year": 2026,
        "month": 9,
        "ransom_demanded_usd": 7200000.0,
        "status": "Active",
        "description": "Targeted payment clearing gateways and leaked internal compliance audit records."
    },
    {
        "id": "INC-2026-004",
        "group": "Akira",
        "victim": "Tokyo Logistics Inc",
        "country": "Japan",
        "sector": "Logistics & Supply Chain",
        "year": 2026,
        "month": 10,
        "ransom_demanded_usd": 2100000.0,
        "status": "Paid",
        "description": "Compromised VPN gateway, compromising automated port container routing databases."
    },
    {
        "id": "INC-2026-005",
        "group": "LockBit 3.0",
        "victim": "BioPharma Nordics",
        "country": "Sweden",
        "sector": "Pharmaceuticals",
        "year": 2026,
        "month": 10,
        "ransom_demanded_usd": 4800000.0,
        "status": "Active",
        "description": "Exfiltrated clinical trial data and demanded ransom for decryption key."
    }
]

def seed_database():
    logger.info(f"Initializing Firestore client for project '{PROJECT_ID}'...")
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection(COLLECTION_NAME)

    for item in SEED_INCIDENTS:
        doc_ref = collection_ref.document(item["id"])
        doc_ref.set(item)
        logger.info(f"Seeded document {item['id']} for victim '{item['victim']}' ({item['group']})")

    logger.info(f"Successfully seeded {len(SEED_INCIDENTS)} incidents into collection '{COLLECTION_NAME}'.")

if __name__ == "__main__":
    seed_database()
