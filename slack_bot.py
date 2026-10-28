"""Run the release ticket Slack bot (Socket Mode: no public URL needed).

Requires SLACK_BOT_TOKEN, SLACK_APP_TOKEN, JIRA_BASE_URL, JIRA_EMAIL,
JIRA_API_TOKEN and ANTHROPIC_API_KEY. See docs/release-ticket-agent.md.
"""

import logging
import os

from dotenv import load_dotenv

from src.release.catalog import Catalog
from src.release.intake import parse_release_text
from src.release.jira_client import JiraClient
from src.release.plan_store import PlanStore
from src.release.service import ReleaseService
from src.release.slack_app import Deps, build_app

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    catalog = Catalog.load()
    if not catalog.slack.get("approvers"):
        logger.warning("No Slack approvers configured in config/release_catalog.yaml: "
                       "plans can be prepared but nobody can approve them.")
    jira = JiraClient()
    service = ReleaseService(catalog, jira, PlanStore())
    user_names: dict[str, str] = {}

    def user_name(user_id: str) -> str:
        if user_id not in user_names:
            try:
                info = app.client.users_info(user=user_id)["user"]
                user_names[user_id] = info.get("real_name") or info.get("name") or user_id
            except Exception:
                user_names[user_id] = user_id
        return user_names[user_id]

    deps = Deps(
        catalog=catalog,
        service=service,
        parse=lambda text: parse_release_text(text, catalog),
        browse=jira.browse_url,
        owner_name=jira.display_name,
        user_name=user_name,
    )
    app = build_app(deps, os.environ["SLACK_BOT_TOKEN"])
    logger.info("Release ticket bot running (Socket Mode)")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
