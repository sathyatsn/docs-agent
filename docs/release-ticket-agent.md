# Release Ticket Agent: design

Automates creating the Jira release tickets (one epic plus its child tasks,
in `CM` by default, or in another project configured per product) for every
Couchbase Mobile product and platform: Couchbase Lite (Android,
iOS, Java, C, .NET), CBL Vector Search, CBL JS, CBL React Native, Sync Gateway
and Edge Server.

## 1. What happens today (from Jira, Oct 2026)

Each release gets an epic, `<Product> [Maintenance] Release Ticket - <version>`,
in `CM`, with child tasks cloned by hand from template epics:

| Template | Release kind | Child tasks |
| --- | --- | --- |
| CM-1251 / CM-1264 | CBL / SGW major (x.y) | GTM, QE, Perf, Support/KB, White-glove, Notify platforms, Licences, Docs, Publish Builds, Web, Cloud Marketplace, Compliance/legal/security (+ Legal, Security, Compliance sign-offs and Threat Modelling since CBL 4.1) |
| CM-1387 | CBL maintenance | QE, Perf, Docs, Publish Builds, Web |
| CM-1348 | SGW maintenance | QE, Perf, Docs, Publish Builds (S3/DockerHub/RHCC), Web |
| CM-1374 | Edge Server maintenance | Same as SGW |

The work is repetitive and goes wrong in the same few ways:

* **Duplicates.** Two epics for the same release: CM-1800/CM-1804 (RN 1.1.2),
  CM-1768/CM-1769 (CBL 4.1.3), CM-1354/CM-1355, CM-1367/CM-1368.
* **Titles drift.** At least six formats for the same thing, e.g.
  `SGW … 3.3.8-GA Sept 18`, `CBL JS … CBL JS 1.0.3 - GA Target Oct 16`,
  `CBL … 3.2.5 .NET -  April 7`. The GA date only lives in the title; `duedate`
  is empty everywhere, so nothing can be sorted or alerted on.
* **Checklists ignore the platform.** The .NET-only 4.1.1 release (CM-1695) and
  CBL JS 1.0.3 (CM-1764) carry the full CBL publish list (Carthage, apt, Maven,
  SPM…), so the build team has to work out which rows apply.
* **Uneven child sets.** CBL maintenance epics have no Perf task (the template
  has one); some children have no description (CM-1771, CM-1779).

## 2. Goals and non-goals

Goals
1. One command (or one YAML file per release train) creates the full, correct
   ticket set for any product, version and platform subset.
2. Safe to re-run: never creates a duplicate, and completes a partly created
   or hand-made release instead of starting over.
3. The process lives in a reviewed config file, not in whoever clones the
   template that week.
4. Platform-aware checklists: rows for platforms that are not shipping are
   pre-marked NA.
5. An audit that finds gaps in open release epics.

6. A TPM can ask in Slack in one line and an approver creates the tickets with one click.

Non-goals (v1): transitioning or closing tickets, creating the FEWD tickets in
the separate `jira.issues.couchbase.com` instance, release notes.

## 3. Architecture

```
Slack /release or @mention ─┐        CLI flags | releases/<train>.yaml ─┐
                            ▼                                          │
     Claude intake: split text into candidates + verbatim quotes       │
                            ▼                                          │
     grounding.py: verify every field against the text                 │
      (anything unverifiable -> reply with reasons; nothing planned)   │
                            ▼                                          ▼
                     ReleaseRequest(s)  ◀───────────────────────────────┘
                            ▼
  release_catalog.yaml ──▶ Planner: tasks, checklists, owners, dates;
                           reconcile with existing epics/children in Jira
                            ▼
              Stored plan + fingerprint  ──▶  shown to reviewer (Slack / CLI)
                            ▼  Approve (allow-listed approver)
              Rebuild (no LLM) ─ fingerprint same? ─ no ─▶ re-post updated plan
                            ▼ yes
              Jira REST: epic, then missing tasks; provenance comment
```

| Module | Responsibility |
| --- | --- |
| `config/release_catalog.yaml` | Products, platforms, task library, checklists, owners, due-date offsets. The only file to edit when the process changes. |
| `src/release/models.py` | `ReleaseRequest`, `PlannedIssue`, `ReleasePlan`; release type is inferred (`x.y`/`x.y.0` = major, otherwise maintenance). |
| `src/release/catalog.py` | Loads and validates the catalog; resolves product aliases; classifies existing epic titles by product and platform scope. |
| `src/release/render.py` | Renders checklists as Jira wiki markup; NA-marks rows whose platforms are not in the release; builds the QE testing matrix for in-scope platforms only. |
| `src/release/planner.py` | Builds the plan, finds existing epics (by agent label, then by legacy title match), marks children that already exist, creates what is missing (epic first, then tasks). |
| `src/release/auditor.py` | Lists open release epics with duplicates, missing tasks, missing GA dates and unassigned tasks. |
| `src/release/intake.py` | Claude splits free text ("SGW 4.1.4 and 4.0.10 GA Dec 10") into candidate releases, each with verbatim evidence quotes. |
| `src/release/grounding.py` | Deterministic checks that each candidate's product, version, platforms and date are written in the request; anything else is rejected. |
| `src/release/plan_store.py`, `service.py` | Stores plans with a fingerprint; prepare -> approve -> apply exactly once; provenance comment. |
| `src/release/slack_app.py`, `slack_format.py`, `slack_bot.py` | Slack bot: `/release` and @mention intake, Block Kit plan, Approve/Cancel buttons, approver allow-list. |
| `src/release/jira_client.py` | JQL search (`/rest/api/3/search/jql`) and issue creation (`/rest/api/2/issue`, so descriptions can be wiki markup). |
| `release_agent.py` | CLI: `create`, `train`, `intake`, `approve`, `cancel`, `audit`, `products`. |

### Key decisions

**Deterministic tickets, LLM only for intake.** The epic description says
"Please do not modify without consultation". Ticket content therefore comes
from the catalog, never from a model. Claude only splits free text into
candidate releases, and section 4 lists the checks every candidate must pass.

**Dry run by default.** Every command prints the plan, including which issues
already exist, and writes nothing until it's approved (`approve <id>`, the Slack
button, or `--apply` for exact `create`/`train` arguments).

**Idempotency through labels plus legacy matching.** Agent-created issues get
`release-agent`, `release-<product>-<version>[-<platforms>]` and
`release-type-<kind>` labels. For epics created by hand, the planner also
searches `summary ~ "Release Ticket"` and the version, then filters by product
regex, an exact version match, and platform scope. So `CBL 4.1.3` never matches
`SGW 4.1.3`, and `CBL 4.0 .NET` (CM-1322) stays separate from `CBL 4.0`
(CM-1303). If one epic matches, only the missing children are added. If more
than one matches, the plan is **blocked** until someone closes the duplicate
or passes `--epic`. Child tasks are matched on their normalised summary
prefix, so `Web updates - 4.1.3` counts as `Web Updates`.

**Partial failure is safe.** The epic is created first and children are
labelled. If a run dies halfway, re-running it finds the epic by label and
creates only the rest.

**Platform scope is part of the release identity.** `--platforms dotnet` gives
`CBL .NET Maintenance Release Ticket - 3.4.4`, a `release-cbl-3.4.4-dotnet`
label, a QE matrix with only .NET, and publish rows for iOS, C, Android and
Java pre-marked `NA (platform not in this release)`.

**The GA date is a field, not only text.** The epic's `duedate` is the GA date.
Each task's `duedate` is GA plus `due_offset_days` (QE −5, Docs −1, Publish/Web
0, Support +14, …), so Jira filters and dashboards can track slippage.

## 4. Quality guarantees

These hold for every path (CLI, YAML train, Slack):

1. **Ticket content is never generated by a model.** Titles, checklists,
   owners and due dates come only from `config/release_catalog.yaml`.
2. **Every extracted field is checked against the requester's words.** Claude
   must return a verbatim quote for each release (and for its GA date).
   `src/release/grounding.py` then checks, without any model involved:
   * the quote appears in the request;
   * the version is written in it;
   * the product named closest before that version is the claimed product
     (so "CBL JS 1.0.4" can't become CBL);
   * the platforms named around the version are exactly the claimed platforms
     (a ".NET-only" release can't silently become a full release, or the
     reverse);
   * the date's day, month and year match what's written. Ambiguous `12/10`
     dates and past dates are rejected. Relative dates ("next Friday") are
     marked for the approver to check.
   * every version number in the request ends up in exactly one release.
3. **All or nothing on intake.** If any part of a request can't be verified,
   nothing is planned. The requester gets the reasons and an example of the
   expected format.
4. **Nothing is written without approval.** Each plan is stored with a SHA-256
   fingerprint of everything shown: issues, text, owners, dates, and what
   already exists. On approval the plan is rebuilt from the stored structured
   requests (Claude is not called again). Tickets are created only if the
   fingerprint is unchanged. If Jira or the catalog changed in between, a
   fresh plan is posted for re-approval.
5. **Exactly once.** A plan moves `pending -> applying` atomically, so double
   clicks and two approvers can't apply it twice. Re-requesting a release
   only fills in missing tickets, and duplicate epics block the plan.
6. **Honest failure reporting.** If Jira fails partway through, the reply lists
   the issues that were created. Requesting the same release again completes
   the rest.
7. **Audit trail.** Each new epic gets a Jira comment with the plan ID, the
   requester, the approver and the original request text.
8. **Fail closed.** If no Slack approvers are configured, nobody can approve.
   Unknown products, platforms or Jira project keys are rejected when the
   catalog loads.

## 5. Using it

### Slack (for TPMs)

```
/release SGW 4.1.4 GA Dec 10
/release SGW 4.1.4 GA Dec 10
         Edge Server 1.0.3 GA Dec 10
         CBL .NET 3.4.4 GA Dec 12
@release-tickets SGW 4.0.10 GA 2026-12-10        (in a thread or channel)
/release help
```

The bot replies with the full plan:
* each epic and task, with "exists CM-123" or "create";
* the owner (display name read from Jira) and due date;
* the quoted text each release and GA date was read from;
* any warnings.

Buttons: **Approve: create N issue(s)** (approvers only, with a confirmation
dialog) and **Cancel** (the requester or an approver). The buttons disappear
as soon as approval starts. The message is then updated with links to the
created epics.

### CLI

```bash
python release_agent.py create --product sgw --version 4.1.4 --ga 2026-12-10   # plan, prints plan id
python release_agent.py approve rp-1a2b3c4d                                    # create exactly that plan
python release_agent.py create --product "Couchbase Lite" --version 3.4.4 --platforms dotnet --ga 2026-12-12 --apply
python release_agent.py train releases/example-train.yaml --show-descriptions
python release_agent.py intake "SGW 4.1.4 and 4.0.10 GA Dec 10"                # plan only; approve separately
python release_agent.py cancel rp-1a2b3c4d
python release_agent.py audit
```

`--offline` plans without contacting Jira, which is useful in CI to check
catalog changes.

### Setup

1. **Jira:** create a service account with create rights in each release project
   (`CM` and any project set per product). Put `JIRA_BASE_URL`, `JIRA_EMAIL`
   and `JIRA_API_TOKEN` in `.env`.
2. **Claude:** set `ANTHROPIC_API_KEY`. `RELEASE_AGENT_MODEL` defaults to
   `claude-opus-5-5`. The intake call enables Anthropic's server-side refusal
   fallback (`fallbacks: "default"`).
3. **Slack app:** at api.slack.com/apps, choose *Create New App* -> *From
   manifest* and paste `slack/manifest.yaml`. Install it to the workspace, then
   copy the Bot token (`xoxb-…`) to `SLACK_BOT_TOKEN`. Under *Basic Information*,
   create an app-level token with `connections:write` and put it in
   `SLACK_APP_TOKEN`. Socket Mode is used, so no public URL is needed.
4. **Approvers:** add the TPMs' / release managers' Slack member IDs to
   `slack.approvers` in the catalog. Optionally restrict `slack.allowed_channels`.
5. **Run:** `python slack_bot.py` on any always-on host (VM or container). Pending
   plans are kept in `config/release_plans.json` (`RELEASE_PLAN_STORE`). Keep
   that file on persistent storage.
6. Invite the bot to the release channel (`/invite @release-tickets`).

### Adding a product or another Jira space

Add an entry under `products:` with:
* its title and aliases;
* a title-match regex;
* its platforms;
* its tasks for `maintenance` and `major`.

To file it outside `CM`, add `jira: {project: KEY}` (optionally with
`epic_issue_type` / `task_issue_type`). Run `pytest` and a `--offline` dry
run before merging. The bot only accepts products that are in the catalog.

## 6. Rollout

1. **Shadow (week 1):**
   * Run `audit` and dry runs for the next release train, and compare them
     with the hand-made tickets.
   * Fill in the `null` owners (PM, GTM, legal, security, compliance) and the
     Slack approvers.
   * Confirm the JS/RN npm publish rows.
2. **Pilot:** TPMs request one maintenance train in Slack (SGW + Edge Server);
   a release manager approves.
3. **Default:** Slack becomes the way to request releases. For large trains,
   use a reviewed `releases/<train>.yaml` (CI runs `train --offline` on the
   PR).
4. Retire the clone-by-hand step. Keep CM-1251/1264/1348/1374/1387 as
   read-only references.

## 7. Phase 2 options

* Create the linked FEWD Download-page and 3rd-party tickets in
  `jira.issues.couchbase.com` and write their links into the Web Updates table.
* Set `fixVersion` and create the Jira version if it's missing.
* Use the existing meeting-notes agent to spot "cut x.y.z" decisions and post
  them to the bot as plans for approval.
* Status roll-up: read each child checklist's `(x)` / `(/)` cells and report
  readiness per release (feeds the mobile release gate checklist).
* Bulk fixes from `audit`: backfill `duedate` from titles and add missing
  children to open epics (each one goes through the same dry-run, then
  `--apply`, flow).
