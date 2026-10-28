---
name: release-tickets
description: Create Couchbase release tickets in Jira (the release epic plus its child tasks such as QE Sign-off, Perf Sign-off, Documentation Tasks, Publish Builds, Web Updates, and the full GA checklist for major releases) from a one-line request, for Couchbase Lite (Android, iOS, Java, C, .NET), CBL Vector Search, CBL JS, CBL React Native, Sync Gateway and Edge Server. Use whenever someone asks to create, cut, open or set up a release ticket, release epic or maintenance release ticket (e.g. "create the release ticket for SGW 4.1.4 GA Dec 10"), or asks to audit open release epics. Uses the Atlassian connector; never invents ticket content.
---

# Release tickets

You turn a TPM's request into Jira release tickets that exactly match the
team's release process. **You do not write ticket content.** Every summary,
description, owner, due date and label comes from `scripts/release_plan.py`
(which reads the process from `release_catalog.yaml`). Your job is to read the
request carefully, run the script, run Jira searches and creates through the
Atlassian connector, and keep the user informed.

Jira site: `couchbasecloud.atlassian.net` (get the cloudId with the connector's
accessible-resources tool if a tool needs it).

## Non-negotiable rules

1. Never create, edit or delete anything in Jira before (a) `verify` returned
   `"ok": true`, (b) you showed the user the plan from `plan`, and (c) the user
   replied with an explicit yes in a **later** message.
2. Copy every field into Jira **exactly** as it appears in `plan.json`. Never
   reword, shorten, translate, reformat or "improve" a summary or description,
   and never add, drop or merge tickets.
3. Never guess. If the request lacks something `verify` needs, or `verify`
   rejects anything, tell the user the reasons word for word and ask them to
   restate the request. Don't patch it up yourself.
4. If a tool call fails, stop and report exactly what was and wasn't created.
   Re-running the whole flow is safe: it only creates what is missing.
5. Report results only from tool output. Never claim a ticket exists without
   its key from Jira.

## Setup (once per conversation)

- Make a new empty working folder (e.g. `/tmp/release-<timestamp>/`). All files
  below live there.
- The script needs Python 3.10+ and PyYAML (`pip install pyyaml` if the import fails).
- `python scripts/release_plan.py products` lists the products, their keys,
  platforms and Jira projects. Use these keys in step 2.

## Flow

### 1. Save the request verbatim
Write the user's request text to `request.txt` exactly as they wrote it.

### 2. Write your reading of it to `candidates.json`
A JSON list with one object per release the user asked for:

```json
[{"product": "sgw", "version": "4.1.4", "platforms": null,
  "ga_date": "2026-12-10", "evidence": "SGW 4.1.4", "ga_date_evidence": "GA Dec 10"}]
```

- `product`: a key from `products`, for the product named with that version.
- `version`: exactly as written.
- `platforms`: only if the request limits the release to particular platforms
  ("CBL .NET 3.4.4" -> `["dotnet"]`); otherwise `null`.
- `ga_date`: `YYYY-MM-DD` only if a date is stated. Resolve "Dec 10" or "next
  Friday" against today's date. Otherwise `null`.
- `evidence`: a span copied character for character from `request.txt` that
  contains the product name and the version.
- `ga_date_evidence`: a span copied character for character that states the
  date, or `null`.

If no version is given, ask for it instead of running anything.

### 3. Verify
```
python scripts/release_plan.py verify --text-file request.txt --candidates candidates.json --out verified.json
```
If `ok` is false, show the user every `rejected` line verbatim. Then ask them
to restate the request, one release per line, e.g.
`SGW 4.1.4 GA Dec 10` / `CBL .NET 3.4.4 GA Dec 12`. Stop here.

### 4. Plan (checks Jira for existing tickets)
```
python scripts/release_plan.py plan --verified verified.json --searches searches.json --out plan.json
```
- **Exit code 3** means the script needs Jira searches. For each entry in
  `queries`:
  - Run its `jql` with the connector's JQL search tool. Fetch all pages and
    request the fields summary, duedate and assignee.
  - Add the result to `searches.json` (create the file if needed) as
    `{"<id>": [{"key": "CM-123", "summary": "...", "duedate": "2026-12-10" or null, "assignee": "<accountId>" or null}]}`.
    Record an empty list if nothing matched.
  - Run `plan` again. Repeat until the exit code is 0. Usually it takes two or
    three rounds, because once an epic is found its child tasks are searched.
- **Exit code 0** prints a Markdown plan and a `fingerprint`.

### 5. Show the plan and ask
Show the user the printed plan unchanged, including every ⚠ warning and 🔎 note
(such as unassigned owners, an epic that already exists, or a relative date to
check). Then ask: *"Reply **yes** to create these N issue(s) in Jira."*
- If the plan shows ⛔ Blocked (duplicate epics already exist), nothing can be
  created for that release. Explain why and suggest closing the duplicate.
- If it says "Nothing to create", say so and stop.
- If the user asks for changes, start again from step 1 with the restated
  request. Never edit `plan.json`.

### 6. On "yes": refresh, then create
1. Delete `searches.json` and re-run step 4 with `--expect-fingerprint <fingerprint>`,
   doing the searches again so the check uses current Jira state.
   **Exit code 4** means Jira or the process changed since the user approved.
   Show the new plan and ask again.
2. For each release in `plan.json` that is not `blocked`, create the issues in
   order with the connector's create-issue tool.
   - The first issue is the epic. If its `action` is `exists`, use its
     `existing_key` as the epic key; if it's `create`, create it first and
     record the new key.
   - Create every issue whose `action` is `create`, with:
     - project = `project`
     - issue type = `issue_type`
     - summary = `summary`
     - description = `description` (Markdown)
     - assignee = `assignee_account_id`, or leave it unassigned if null
     - due date (`duedate`) = `due_date`
     - labels = `labels`
     - for tasks, parent = the epic key
   - If the create tool has no parameter for a field, pass it as an
     additional Jira field (`duedate`, `labels`, `parent: {"key": ...}`) or set
     it right after with the connector's edit-issue tool. Never skip a field
     silently.
3. If the connector can add comments, comment on each newly created epic:
   `Created with the release-tickets skill. Requested by <user> in Claude: "<request.txt>"`.

### 7. Check what was created
Read back every created issue with the connector's get-issue tool. Write
`created.json` as a list of:
`{"key", "summary", "project", "issue_type", "parent", "assignee_account_id", "due_date", "labels", "description"}`
(`parent` is the parent's key; `description` as returned). Then run:
```
python scripts/release_plan.py check --plan plan.json --created created.json
```
Report to the user:
- links to each epic (`https://couchbasecloud.atlassian.net/browse/<KEY>`);
- the keys you created;
- the `check` result.

If `check` lists problems, show them verbatim and ask before fixing anything.

## Auditing open release epics

When asked "are our release tickets in order?" or similar:
```
python scripts/release_plan.py audit --searches audit_searches.json
```
Run the requested searches the same way as in step 4 until the exit code is 0,
then show the report: duplicates, missing tasks, missing GA dates and
unassigned tasks. The audit only reads; don't fix anything unless asked, and
then only through the create flow above.
