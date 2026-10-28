"""Slack handlers for the release ticket agent.

Flow: a TPM writes `/release SGW 4.1.4 GA Dec 10` (or @mentions the bot). The
bot verifies the request, posts the exact plan with Approve/Cancel buttons, and
only an approver listed in config/release_catalog.yaml can create the tickets.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from src.release.catalog import Catalog
from src.release.intake import IntakeResult
from src.release.service import ReleaseService
from src.release.slack_format import (
    APPROVE_ACTION, CANCEL_ACTION, esc, plan_blocks, rejection_text, result_blocks)

logger = logging.getLogger(__name__)

MAX_REQUEST_CHARS = 3000
MAX_RELEASES = 10
EXAMPLES = ("`SGW 4.1.4 GA Dec 10`\n`Edge Server 1.0.3 GA 2026-12-10`\n"
            "`CBL .NET 3.4.4 GA Dec 12`  (single-platform release)")


def help_text(catalog: Catalog) -> str:
    products = "\n".join(f"• *{esc(p['title'])}*: {esc(p['name'])}" for p in catalog.products.values())
    return (
        "*Release ticket agent*: describe the release(s) and I'll prepare the Jira tickets for approval.\n"
        f"{EXAMPLES}\n\nOne release per line. Several lines = one plan for the whole train.\n"
        f"*Products:*\n{products}\n\n"
        "Nothing is created until an approver clicks *Approve*. Re-requesting a release only adds missing tickets."
    )


@dataclass
class Deps:
    catalog: Catalog
    service: ReleaseService
    parse: Callable[[str], IntakeResult]
    browse: Callable[[str], str]
    owner_name: Callable[[str], str]
    user_name: Callable[[str], str]

    @property
    def approvers(self) -> set[str]:
        return set(self.catalog.slack.get("approvers") or [])

    @property
    def ttl_hours(self) -> int:
        return self.catalog.slack.get("plan_ttl_hours", 24)


def handle_request(text: str, user_id: str, channel_id: str, reply: Callable[..., None], deps: Deps) -> None:
    text = (text or "").strip()
    if not text or text.lower() in ("help", "?"):
        reply(text=help_text(deps.catalog))
        return
    allowed = deps.catalog.slack.get("allowed_channels") or []
    if allowed and channel_id not in allowed:
        reply(text="Release tickets can't be requested from this channel.")
        return
    if len(text) > MAX_REQUEST_CHARS:
        reply(text=f"That request is too long (max {MAX_REQUEST_CHARS} characters). Please split it up.")
        return

    try:
        result = deps.parse(text)
        if result.rejected:
            reply(text=rejection_text(result.rejected, EXAMPLES))
            return
        if not result.releases:
            reply(text=rejection_text(["No release found in the request."], EXAMPLES))
            return
        if len(result.releases) > MAX_RELEASES:
            reply(text=f"That's {len(result.releases)} releases; please split it into requests of at most {MAX_RELEASES}.")
            return
        prepared = deps.service.prepare(
            result.releases,
            requested_by=f"{deps.user_name(user_id)} (Slack {user_id})",
            source="slack",
            source_text=text,
            context={"channel": channel_id, "user": user_id},
        )
    except Exception as e:
        logger.exception("Release request failed")
        reply(text=f":rotating_light: I couldn't prepare that request ({esc(str(e))}). Nothing was created.")
        return

    reply(text=f"Release ticket plan {prepared.stored.id}",
          blocks=plan_blocks(prepared, f"<@{user_id}>", deps.browse, deps.owner_name, deps.ttl_hours))


def _without_actions(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks or [] if b.get("type") != "actions"]


def handle_action(action_id: str, plan_id: str, user_id: str, original_blocks: list[dict],
                  respond: Callable[..., None], deps: Deps) -> None:
    actor = f"<@{user_id}>"
    if action_id == APPROVE_ACTION:
        if user_id not in deps.approvers:
            respond(replace_original=False, response_type="ephemeral",
                    text="Only release approvers can create tickets. Ask one of them to approve this plan.")
            return
        # Remove the buttons straight away so nobody clicks twice while Jira is being written.
        respond(replace_original=True, text=f"Creating tickets for {plan_id}…",
                blocks=_without_actions(original_blocks) + [
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f":hourglass_flowing_sand: Approved by {actor}; creating tickets…"}]}])
        result = deps.service.approve(plan_id, f"{deps.user_name(user_id)} (Slack {user_id})")
    elif action_id == CANCEL_ACTION:
        stored = deps.service.store.get(plan_id)
        requester = stored.context.get("user") if stored else None
        if user_id not in deps.approvers and user_id != requester:
            respond(replace_original=False, response_type="ephemeral",
                    text="Only the requester or a release approver can cancel this plan.")
            return
        result = deps.service.cancel(plan_id, f"{deps.user_name(user_id)} (Slack {user_id})")
    else:
        return

    respond(replace_original=True, text=f"Plan {plan_id}: {result.status}",
            blocks=_without_actions(original_blocks) + result_blocks(result, plan_id, actor, deps.browse))
    if result.replacement:
        respond(replace_original=False, response_type="in_channel",
                text=f"Updated plan {result.replacement.stored.id}",
                blocks=plan_blocks(result.replacement, actor, deps.browse, deps.owner_name, deps.ttl_hours))


def build_app(deps: Deps, bot_token: str, **app_options):
    from slack_bolt import App

    app = App(token=bot_token, **app_options)

    @app.command("/release")
    def on_command(ack, command, respond):
        ack()
        handle_request(command.get("text", ""), command["user_id"], command["channel_id"],
                       lambda **kw: respond(response_type="in_channel", **kw), deps)

    @app.event("app_mention")
    def on_mention(event, say):
        text = event.get("text", "")
        # Drop the leading <@BOT> mention.
        text = text.split(">", 1)[1] if text.startswith("<@") and ">" in text else text
        thread = event.get("thread_ts") or event["ts"]
        handle_request(text, event["user"], event["channel"], lambda **kw: say(thread_ts=thread, **kw), deps)

    def on_action(ack, body, action, respond):
        ack()
        handle_action(action["action_id"], action["value"], body["user"]["id"],
                      body.get("message", {}).get("blocks", []), respond, deps)

    app.action(APPROVE_ACTION)(on_action)
    app.action(CANCEL_ACTION)(on_action)
    return app
