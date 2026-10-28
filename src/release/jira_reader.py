"""Read-only Jira access for the planner, fed by searches Claude runs through the Atlassian connector.

The planner only ever calls `search(jql, fields)`. RecordedJira answers from
results Claude already fetched; any JQL it doesn't have yet is recorded in
`missing`, so the skill can tell Claude exactly which searches to run next.
No credentials are involved anywhere in this package.
"""

import hashlib
from typing import Protocol


def query_id(jql: str) -> str:
    """Short stable id for a JQL string, so results can be recorded without retyping the query."""
    return "q" + hashlib.sha1(jql.encode()).hexdigest()[:8]


class JiraReader(Protocol):
    def search(self, jql: str, fields: list[str]) -> list[dict]: ...


class RecordedJira:
    """Answers searches from `results`, keyed by query_id(jql)."""

    def __init__(self, results: dict[str, list[dict]] | None = None):
        self.results = results or {}
        self.missing: list[dict] = []  # [{"id": ..., "jql": ...}]

    def search(self, jql: str, fields: list[str]) -> list[dict]:
        qid = query_id(jql)
        if qid not in self.results:
            if all(m["id"] != qid for m in self.missing):
                self.missing.append({"id": qid, "jql": jql})
            return []
        return [_as_issue(i) for i in self.results[qid]]


def _as_issue(item: dict) -> dict:
    """Accept either a raw Jira issue ({key, fields: {...}}) or a flat {key, summary, ...} record."""
    if "fields" in item:
        return item
    return {"key": item["key"], "fields": {k: v for k, v in item.items() if k != "key"}}
