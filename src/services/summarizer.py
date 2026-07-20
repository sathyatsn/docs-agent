import json
import os

import anthropic

from src.models.meeting import MeetingEvent, MeetingNotes

SYSTEM_PROMPT = """\
You are a meeting notes assistant. Given raw meeting transcript/notes, \
produce structured meeting notes.

Return a JSON object with these fields:
- summary: A concise 2-4 sentence summary of the meeting
- action_items: List of specific action items with owners where mentioned
- decisions: List of decisions made during the meeting
- key_topics: List of main topics discussed
- follow_ups: List of items that need follow-up

Be concise and actionable. Use bullet-point style for list items.\
"""


def summarize_transcript(
    transcript: str,
    meeting: MeetingEvent,
) -> MeetingNotes:
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
    client = anthropic.Anthropic()

    user_prompt = (
        f"Meeting: {meeting.title}\n"
        f"Date: {meeting.start_time}\n"
        f"Attendees: {', '.join(meeting.attendees)}\n\n"
        f"Transcript/Notes:\n{transcript}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return MeetingNotes(summary=text, attendees=meeting.attendees)

    data = json.loads(text[start:end])
    return MeetingNotes(
        summary=data.get("summary", ""),
        action_items=data.get("action_items", []),
        decisions=data.get("decisions", []),
        key_topics=data.get("key_topics", []),
        follow_ups=data.get("follow_ups", []),
        attendees=meeting.attendees,
    )
