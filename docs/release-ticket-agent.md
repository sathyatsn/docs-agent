# Release Ticket Agent: design

Automates creating the Jira release tickets (one epic plus its child tasks in
`CM`) for every Couchbase Mobile product and platform: Couchbase Lite (Android,
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

Non-goals (v1): transitioning or closing tickets, creating the FEWD tickets in
the separate `jira.issues.couchbase.com` instance, release notes.

## 3. Architecture

```
CLI flags | releases/<train>.yaml | free text ──(Claude intake)──┐
                                                                 ▼
                                                     ReleaseRequest(s)
                                                                 ▼
  config/release_catalog.yaml ──▶ Planner: pick tasks, render checklists,
                                  then reconcile with existing Jira epics/children
                                                                 ▼
                                             Dry-run plan (always printed)
                                                                 ▼ --apply
                                  Jira REST: epic first, then missing child tasks
```

| Module | Responsibility |
| --- | --- |
| `config/release_catalog.yaml` | Products, platforms, task library, checklists, owners, due-date offsets. The only file to edit when the process changes. |
| `src/release/models.py` | `ReleaseRequest`, `PlannedIssue`, `ReleasePlan`; release type is inferred (`x.y`/`x.y.0` = major, otherwise maintenance). |
| `src/release/catalog.py` | Loads and validates the catalog; resolves product aliases; classifies existing epic titles by product and platform scope. |
| `src/release/render.py` | Renders checklists as Jira wiki markup; NA-marks rows whose platforms are not in the release; builds the QE testing matrix for in-scope platforms only. |
| `src/release/planner.py` | Builds the plan, finds existing epics (by agent label, then by legacy title match), marks children that already exist, creates what is missing (epic first, then tasks). |
| `src/release/auditor.py` | Lists open release epics with duplicates, missing tasks, missing GA dates and unassigned tasks. |
| `src/release/intake.py` | Optional: Claude turns "cut SGW 4.1.4 and 4.0.10 for Dec 10, plus a .NET-only 3.4.4" into `ReleaseRequest`s. |
| `src/release/jira_client.py` | JQL search (`/rest/api/3/search/jql`) and issue creation (`/rest/api/2/issue`, so descriptions can be wiki markup). |
| `release_agent.py` | CLI: `create`, `train`, `intake`, `audit`, `products`. |

### Key decisions

**Deterministic tickets, LLM only for intake.** The epic description says
"Please do not modify without consultation". Ticket content therefore comes
from the catalog, never from a model. Claude only turns free text into
structured requests, which are validated against the catalog (enum products,
version regex) and shown as a dry-run plan before anything is written.

**Dry run by default.** Every command prints the plan, including which issues
already exist, and writes nothing without `--apply`.

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

## 4. Usage

```bash
python release_agent.py products
python release_agent.py create --product sgw --version 4.1.4 --ga 2026-12-10            # dry run
python release_agent.py create --product "Couchbase Lite" --version 3.4.4 --platforms dotnet --ga 2026-12-12 --apply
python release_agent.py train releases/example-train.yaml --show-descriptions
python release_agent.py intake "SGW 4.1.4 and 4.0.10 GA Dec 10; CBL .NET 3.4.4 a week later"
python release_agent.py audit
```

`--offline` plans without contacting Jira (useful in CI to check catalog changes).

Configuration (`.env`): `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
(a token from a user or service account with create rights in `CM`), and
optionally `RELEASE_AGENT_MODEL` (default `claude-opus-5-5`) for intake.

## 5. Rollout

1. **Shadow (week 1):** run `audit` and dry runs for the next release train;
   compare with hand-made tickets. Fill in the `null` owners in the catalog
   (PM, GTM, legal, security, compliance) and confirm the JS/RN npm publish rows.
2. **Pilot:** use `--apply` for one maintenance train (SGW + Edge Server), then CBL.
3. **Default:** creating a release becomes "open a PR adding `releases/<train>.yaml`".
   A CI job runs `train --offline` on the PR and `train --apply` on merge, so
   every release has a reviewed record in git.
4. Retire the clone-by-hand step; keep CM-1251/1264/1348/1374/1387 as read-only
   references or close them.

## 6. Phase 2 options

* Create the linked FEWD Download-page and 3rd-party tickets in
  `jira.issues.couchbase.com` and write their links into the Web Updates table.
* Set `fixVersion` and create the Jira version if it's missing.
* Post the plan and created links to the release Slack channel. Use the
  existing meeting-notes agent to spot "cut x.y.z" decisions and turn them into
  `intake` dry runs for approval.
* Status roll-up: read each child checklist's `(x)` / `(/)` cells and report
  readiness per release (feeds the mobile release gate checklist).
* Bulk fixes from `audit`: backfill `duedate` from titles and add missing
  children to open epics (each one goes through the same dry-run, then
  `--apply`, flow).
