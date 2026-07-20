import logging
import os

from src.models.meeting import MeetingEvent
from src.services.calendar_service import get_recent_meetings
from src.services.doc_mapper import find_target_doc
from src.services.docs_service import create_notes_doc, write_notes_to_doc
from src.services.drive_service import find_gemini_notes
from src.services.state_store import load_processed, mark_processed
from src.services.summarizer import summarize_transcript

logger = logging.getLogger(__name__)


def process_meetings() -> list[dict]:
    """Main agent loop: find recent meetings, fetch notes, summarize, and write to docs."""
    processed = load_processed()
    meetings = get_recent_meetings()
    results = []

    for meeting in meetings:
        if meeting.event_id in processed:
            logger.info("Skipping already-processed meeting: %s", meeting.title)
            continue

        result = _process_single_meeting(meeting)
        if result:
            results.append(result)
            mark_processed(meeting.event_id)

    return results


def _process_single_meeting(meeting: MeetingEvent) -> dict | None:
    logger.info("Processing meeting: %s", meeting.title)

    meeting_date = meeting.start_time[:10]
    transcript = find_gemini_notes(meeting.title, meeting_date)
    if not transcript:
        logger.warning("No Gemini notes found for: %s", meeting.title)
        return None

    logger.info("Found Gemini notes, summarizing with Claude...")
    notes = summarize_transcript(transcript, meeting)

    target_doc_id = find_target_doc(meeting.title)
    if target_doc_id:
        logger.info("Appending notes to existing doc: %s", target_doc_id)
        doc_url = write_notes_to_doc(target_doc_id, meeting, notes)
    else:
        logger.info("No matching doc found, creating new document")
        folder_id = os.getenv("OUTPUT_FOLDER_ID") or None
        doc_url = create_notes_doc(meeting, notes, folder_id)

    logger.info("Notes written: %s", doc_url)
    return {
        "meeting": meeting.title,
        "date": meeting.start_time,
        "doc_url": doc_url,
        "action_items_count": len(notes.action_items),
        "decisions_count": len(notes.decisions),
    }
