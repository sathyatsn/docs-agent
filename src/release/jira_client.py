import logging
import os
from datetime import date

import requests

logger = logging.getLogger(__name__)


class JiraClient:
    """Minimal Jira Cloud client: JQL search (v3) and issue creation (v2, wiki markup)."""

    def __init__(self, base_url: str | None = None, email: str | None = None,
                 api_token: str | None = None):
        self.base_url = (base_url or os.environ["JIRA_BASE_URL"]).rstrip("/")
        self.session = requests.Session()
        self.session.auth = (email or os.environ["JIRA_EMAIL"],
                             api_token or os.environ["JIRA_API_TOKEN"])
        self.session.headers["Accept"] = "application/json"

    def search(self, jql: str, fields: list[str]) -> list[dict]:
        issues, token = [], None
        while True:
            params = {"jql": jql, "fields": ",".join(fields), "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            resp = self.session.get(f"{self.base_url}/rest/api/3/search/jql", params=params)
            resp.raise_for_status()
            body = resp.json()
            issues.extend(body.get("issues", []))
            token = body.get("nextPageToken")
            if not token or body.get("isLast", True):
                return issues

    def create_issue(self, project: str, issue_type: str, summary: str, description: str,
                     labels: list[str], assignee_id: str | None = None,
                     due_date: date | None = None, parent_key: str | None = None) -> str:
        fields = {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": description,
            "labels": labels,
        }
        if assignee_id:
            fields["assignee"] = {"accountId": assignee_id}
        if due_date:
            fields["duedate"] = due_date.isoformat()
        if parent_key:
            fields["parent"] = {"key": parent_key}

        resp = self.session.post(f"{self.base_url}/rest/api/2/issue", json={"fields": fields})
        if not resp.ok:
            logger.error("Jira create failed (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()["key"]

    def browse_url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"
