# Release tickets skill: design

A TPM asks Claude, in one sentence, to create release tickets. Claude creates
the Jira release epic and its child tasks (in `CM` by default, or another
project configured per product) through the Atlassian connector. It covers
every Couchbase Mobile product and platform: Couchbase Lite (Android, iOS,
Java, C, .NET), CBL Vector Search, CBL JS, CBL React Native, Sync Gateway and
Edge Server. No API keys, tokens or bots are involved.

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
  SPM…), so whoever works the ticket has to work out which rows apply.
* **Uneven child sets.** CBL maintenance epics have no Perf task (the template
  has one); some children have no description (CM-1771, CM-1779).

## 2. Goals and non-goals

Goals
1. One prompt to Claude creates the full, correct ticket set for any product,
   version and platform subset, or for a whole release train.
2. No hallucinated content: every ticket field comes from the reviewed
   process file, and every detail taken from the prompt is checked against
   the prompt's own words.
3. Safe to repeat: never creates a duplicate, and completes a partly created
   or hand-made release instead of starting over.
4. Platform-aware checklists: rows for platforms that are not shipping are
   pre-marked NA.
5. An audit that finds gaps in open release epics.

Non-goals: transitioning or closing tickets, the FEWD tickets in
`jira.issues.couchbase.com`, release notes.

## 3. How it works

```
TPM: "Create the release tickets for SGW 4.1.4 GA Dec 10 and CBL .NET 3.4.4 GA Dec 12"
                                   │
Claude (release-tickets skill) ────┤ 1. writes its reading of the request, with verbatim quotes
                                   ▼
release_plan.py verify ──────────── 2. checks every product/version/platform/date against the words
                                   │    (anything unverifiable -> Claude asks the TPM; nothing planned)
                                   ▼
release_plan.py plan ◀── Jira search results (Claude runs the JQL it asks for, via the connector)
                                   │ 3. exact tickets from release_catalog.yaml + what already exists
                                   ▼
Claude shows the plan ── TPM replies "yes"
                                   ▼
plan --expect-fingerprint ───────── 4. re-checks Jira; if anything changed, show again
                                   ▼
Claude creates the issues via the Atlassian connector, copying plan.json field by field
                                   ▼
release_plan.py check ───────────── 5. reads the created issues back and compares them with the plan
```

| Piece | Role |
| --- | --- |
| `.claude/skills/release-tickets/SKILL.md` | The procedure Claude follows, with its non-negotiable rules. |
| `.claude/skills/release-tickets/scripts/release_plan.py` | Deterministic helper with `verify`, `plan`, `check`, `audit` and `products` commands. No network access, no credentials. |
| `config/release_catalog.yaml` | The release process: products, platforms, tasks, checklists, owners (Jira account ID + name), due-date offsets, and the Jira project per product. |
| `src/release/grounding.py` | Checks Claude's reading of the request against the exact text. |
| `src/release/planner.py`, `render.py` | Build the exact tickets (Markdown descriptions); reconcile with existing epics and children; fingerprint the plan. |
| `src/release/jira_reader.py` | Feeds the planner the connector's search results, and lists any searches still needed. |
| `src/release/auditor.py` | Reports duplicates, missing tasks, missing GA dates and unassigned tasks. |
| `scripts/build_skill.py` | Packages the skill as `dist/release-tickets.zip` for uploading to Claude. |
| `release_agent.py` | Offline preview CLI for catalog changes. |

## 4. Quality guarantees

1. **Claude never writes ticket content.** Titles, descriptions, checklists,
   owners, due dates and labels come only from the catalog, through
   `plan.json`. The skill forbids rewording any field.
2. **Every detail taken from the prompt is verified.** For each release Claude
   must quote the request verbatim. `verify` then checks, without any model
   involved:
   * the quote appears in the request;
   * the version is written in it;
   * the product named closest before that version is the claimed one ("CBL
     JS 1.0.4" can't become CBL);
   * the platforms named around the version are exactly the claimed
     platforms (a .NET-only release can't silently become a full one, or the
     reverse);
   * the date's day, month and year match what's written. Ambiguous `12/10`
     dates and past dates are rejected. Relative dates are marked for the TPM
     to check.
   * no version number in the request is left unaccounted for.

   If anything fails, nothing is planned and the TPM gets the reasons.
3. **Nothing is created without an explicit "yes"** to a plan the TPM has seen.
4. **What was approved is what gets created.** The plan is fingerprinted. On
   "yes", Jira is searched again, and creation proceeds only if the
   fingerprint is unchanged. Otherwise the new plan is shown.
5. **No duplicates.** Existing epics are found by label and by title (product,
   exact version and platform scope). Existing children are matched by name,
   and only missing tickets are created. Duplicate epics block the release
   until someone resolves them. Existing tickets are shown with their real
   Jira titles and are never modified.
6. **Read-back check.** After creation, `check` compares every created issue
   with the plan: summary, project, type, parent, assignee, due date, labels,
   and every checklist row in the description. Any difference is reported.
7. **Audit trail.** Agent-created issues carry the `release-agent` label plus
   a release label, and the epic gets a comment with the original request.

These guarantees are covered by `pytest`: 45 tests, including an end-to-end
run of the skill flow and tests that feed in wrong readings and fields that
weren't copied exactly.

## 5. Using it

### For TPMs
In Claude (with the Atlassian connector connected and the skill enabled), ask,
for example:

> Create the release tickets for SGW 4.1.4 GA Dec 10

> Create release tickets: SGW 4.1.4 GA Dec 10, Edge Server 1.0.3 GA Dec 10, CBL .NET 3.4.4 GA Dec 12

> Are our open release tickets in order?

Claude replies with the plan: each ticket marked "exists CM-123" or "create",
with owners, due dates and the words each value was read from. Reply **yes**
to create. Use one release per line or clause when a request mixes
platform-specific and full releases.

### Rollout to the team (one time)
1. **Connector:** each TPM connects the Atlassian connector in Claude (Settings
   -> Connectors) with their own Jira login. Their Jira permissions apply: they
   need create rights in `CM`.
2. **Skill:** publish it to the organisation plugin library so every TPM can
   install it: run `python scripts/build_skill.py --plugin-dir <folder outside the repo>`
   and publish that folder. Alternatively, upload `dist/release-tickets.zip`
   as a personal skill (Settings -> Capabilities -> Skills). In Claude Code
   inside this repo, the skill is picked up automatically from
   `.claude/skills/`. Code execution must be enabled, because the skill runs
   a Python script.
3. **Owners:** these are filled in from the 2026 major releases in Jira. The
   source tickets are cited in `config/release_catalog.yaml`, and every owner
   was an active Jira account on 2026-10-28. The exception is **PM for CBL, CBL
   JS, CBL Vector Search and SGW**: the previous owner's Jira account is
   deactivated, so those tasks (White Glove Testing, Notify Changes to
   Platforms, Licences) are created unassigned and flagged until a new owner
   is set under `people.pm`.
4. **Pilot:** ask for one maintenance release, compare it with a hand-made one,
   and then make the skill the standard way to create release tickets.

### Changing the process
Edit `config/release_catalog.yaml` in a PR. Preview the result with
`python release_agent.py preview --product sgw --version 4.1.4 --ga 2026-12-10 --show-descriptions`,
run `pytest`, then rebuild and re-upload the skill zip.

To add a product, add an entry under `products:` with:
* its title and aliases;
* a title-match regex;
* its platforms;
* its tasks for `maintenance` and `major`.

To file it in another Jira space, add `jira: {project: KEY}`. Claude only
accepts products that are in the catalog.

## 6. Phase 2 options

* Create the linked FEWD Download-page and 3rd-party tickets and write their
  links into the Web Updates table.
* Set `fixVersion` and create the Jira version if it's missing.
* Status roll-up: read each child checklist's ❌ / ✅ cells and report
  readiness per release.
* Use the meeting-notes agent to spot "cut x.y.z" decisions and suggest them
  to the TPM as a ready-made request.
