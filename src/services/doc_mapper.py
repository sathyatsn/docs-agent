import json
import os

from googleapiclient.discovery import build

from src.services.google_auth import get_credentials

MAPPING_FILE = "config/meeting_doc_map.json"


def load_mappings() -> dict:
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE) as f:
            return json.load(f)
    return {}


def save_mappings(mappings: dict) -> None:
    os.makedirs(os.path.dirname(MAPPING_FILE), exist_ok=True)
    with open(MAPPING_FILE, "w") as f:
        json.dump(mappings, f, indent=2)


def find_target_doc(meeting_title: str) -> str | None:
    """Find the Google Doc ID where notes for this meeting should go.

    Resolution order:
    1. Exact match in the mapping file (meeting title → doc ID)
    2. Keyword/partial match in the mapping file
    3. Search Google Drive for a doc whose name matches the meeting title
    """
    mappings = load_mappings()

    normalized_title = meeting_title.lower().strip()
    for pattern, doc_id in mappings.items():
        if pattern.lower() == normalized_title:
            return doc_id

    for pattern, doc_id in mappings.items():
        if pattern.lower() in normalized_title or normalized_title in pattern.lower():
            return doc_id

    return _search_drive_for_doc(meeting_title)


def _search_drive_for_doc(meeting_title: str) -> str | None:
    """Search Google Drive for documents matching the meeting title."""
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    keywords = [w for w in meeting_title.split() if len(w) > 2]
    if not keywords:
        return None

    name_clauses = " and ".join(
        f"name contains '{kw.replace(chr(39), '')}'" for kw in keywords[:3]
    )
    query = (
        f"{name_clauses} "
        f"and mimeType = 'application/vnd.google-apps.document' "
        f"and trashed = false"
    )

    results = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=5,
    ).execute()

    files = results.get("files", [])
    if not files:
        return None

    return files[0]["id"]


def add_mapping(meeting_title_pattern: str, doc_id: str) -> None:
    """Add or update a meeting-to-document mapping."""
    mappings = load_mappings()
    mappings[meeting_title_pattern] = doc_id
    save_mappings(mappings)
