from googleapiclient.discovery import build

from src.models.meeting import MeetingEvent, MeetingNotes
from src.services.google_auth import get_credentials


def write_notes_to_doc(
    doc_id: str,
    meeting: MeetingEvent,
    notes: MeetingNotes,
) -> str:
    """Append structured meeting notes to an existing Google Doc.

    Returns the document URL.
    """
    creds = get_credentials()
    service = build("docs", "v1", credentials=creds)

    doc = service.documents().get(documentId=doc_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1

    requests = _build_insert_requests(end_index, meeting, notes)
    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"


def create_notes_doc(
    meeting: MeetingEvent,
    notes: MeetingNotes,
    folder_id: str | None = None,
) -> str:
    """Create a new Google Doc with the meeting notes.

    Returns the document URL.
    """
    creds = get_credentials()
    docs_service = build("docs", "v1", credentials=creds)

    title = f"Meeting Notes - {meeting.title} - {meeting.start_time[:10]}"
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    if folder_id:
        drive_service = build("drive", "v3", credentials=creds)
        drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            fields="id, parents",
        ).execute()

    requests = _build_insert_requests(1, meeting, notes)
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"


def _build_insert_requests(
    start_index: int,
    meeting: MeetingEvent,
    notes: MeetingNotes,
) -> list[dict]:
    """Build Google Docs API batchUpdate requests for inserting meeting notes.

    Inserts are built in reverse order because each insert shifts subsequent
    indices. Building bottom-up with a fixed start_index avoids recalculating.
    """
    sections = []

    sections.append(f"\n{'=' * 60}\n")
    sections.append(f"Meeting: {meeting.title}\n")
    sections.append(f"Date: {meeting.start_time}\n")
    if notes.attendees:
        sections.append(f"Attendees: {', '.join(notes.attendees)}\n")
    sections.append(f"\n")

    if notes.summary:
        sections.append(f"Summary\n")
        sections.append(f"{notes.summary}\n\n")

    if notes.key_topics:
        sections.append(f"Key Topics\n")
        for topic in notes.key_topics:
            sections.append(f"  - {topic}\n")
        sections.append(f"\n")

    if notes.action_items:
        sections.append(f"Action Items\n")
        for item in notes.action_items:
            sections.append(f"  [ ] {item}\n")
        sections.append(f"\n")

    if notes.decisions:
        sections.append(f"Decisions\n")
        for decision in notes.decisions:
            sections.append(f"  - {decision}\n")
        sections.append(f"\n")

    if notes.follow_ups:
        sections.append(f"Follow-ups\n")
        for follow_up in notes.follow_ups:
            sections.append(f"  - {follow_up}\n")
        sections.append(f"\n")

    full_text = "".join(sections)

    requests = [
        {
            "insertText": {
                "location": {"index": start_index},
                "text": full_text,
            }
        }
    ]

    heading_ranges = _find_heading_ranges(full_text, start_index)
    for offset, length in heading_ranges:
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": offset,
                    "endIndex": offset + length,
                },
                "paragraphStyle": {"namedStyleType": "HEADING_2"},
                "fields": "namedStyleType",
            }
        })

    title_line = f"Meeting: {meeting.title}\n"
    title_offset = full_text.find(title_line)
    if title_offset != -1:
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": start_index + title_offset,
                    "endIndex": start_index + title_offset + len(title_line),
                },
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "fields": "namedStyleType",
            }
        })

    return requests


def _find_heading_ranges(
    text: str,
    base_index: int,
) -> list[tuple[int, int]]:
    headings = ["Summary\n", "Key Topics\n", "Action Items\n", "Decisions\n", "Follow-ups\n"]
    ranges = []
    for heading in headings:
        pos = text.find(heading)
        if pos != -1:
            ranges.append((base_index + pos, len(heading)))
    return ranges
