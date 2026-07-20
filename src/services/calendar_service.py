import os
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from src.models.meeting import MeetingEvent
from src.services.google_auth import get_credentials


def get_recent_meetings() -> list[MeetingEvent]:
    lookback_hours = int(os.getenv("MEETING_LOOKBACK_HOURS", "2"))
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(hours=lookback_hours)).isoformat()
    time_max = now.isoformat()

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    meetings = []
    for event in events_result.get("items", []):
        if not _is_google_meet_event(event):
            continue

        attendees = [
            a.get("email", "")
            for a in event.get("attendees", [])
            if not a.get("self", False)
        ]

        meet_link = ""
        conference = event.get("conferenceData", {})
        for entry in conference.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "")
                break

        meetings.append(MeetingEvent(
            event_id=event["id"],
            title=event.get("summary", "Untitled Meeting"),
            start_time=event["start"].get("dateTime", event["start"].get("date", "")),
            end_time=event["end"].get("dateTime", event["end"].get("date", "")),
            attendees=attendees,
            meet_link=meet_link,
            description=event.get("description", ""),
        ))

    return meetings


def _is_google_meet_event(event: dict) -> bool:
    conference = event.get("conferenceData", {})
    solution = conference.get("conferenceSolution", {})
    return solution.get("key", {}).get("type") == "hangoutsMeet"
