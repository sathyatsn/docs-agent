from dataclasses import dataclass, field


@dataclass
class MeetingNotes:
    summary: str = ""
    action_items: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    key_topics: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)


@dataclass
class MeetingEvent:
    event_id: str
    title: str
    start_time: str
    end_time: str
    attendees: list[str] = field(default_factory=list)
    meet_link: str = ""
    description: str = ""
