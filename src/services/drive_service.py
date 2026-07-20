from googleapiclient.discovery import build

from src.services.google_auth import get_credentials


def find_gemini_notes(meeting_title: str, meeting_date: str) -> str | None:
    """Search Google Drive for Gemini meeting notes matching the meeting.

    Gemini note taker saves transcripts to Google Drive with the meeting
    title in the filename. We search by name and date to find the right one.
    """
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    sanitized_title = meeting_title.replace("'", "\\'")
    query = (
        f"name contains '{sanitized_title}' "
        f"and mimeType = 'application/vnd.google-apps.document' "
        f"and modifiedTime >= '{meeting_date}T00:00:00'"
    )

    results = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=5,
    ).execute()

    files = results.get("files", [])
    if not files:
        return None

    doc_id = files[0]["id"]
    return _read_doc_content(service, doc_id)


def _read_doc_content(drive_service, doc_id: str) -> str:
    """Export a Google Doc as plain text."""
    content = drive_service.files().export(
        fileId=doc_id,
        mimeType="text/plain",
    ).execute()
    if isinstance(content, bytes):
        return content.decode("utf-8")
    return content
