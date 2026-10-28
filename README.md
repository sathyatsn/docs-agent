# Meeting Notes Agent

Automated meeting notes agent that picks up Gemini note taker transcripts from Google Meet, summarizes them with Claude, and writes structured notes to your Google Docs.

## How It Works

1. **Calendar Hook** - Polls Google Calendar for recently completed Google Meet meetings
2. **Transcript Fetch** - Finds the Gemini note taker transcript for each meeting in Google Drive
3. **AI Summarization** - Sends the transcript to Claude for structured summarization (summary, action items, decisions, key topics, follow-ups)
4. **Doc Writing** - Writes the structured notes to the mapped Google Doc, or creates a new one

## Setup

### 1. Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable these APIs:
   - Google Calendar API
   - Google Drive API
   - Google Docs API
4. Create OAuth 2.0 credentials:
   - Go to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Choose **Desktop app**
   - Download the JSON file and save it as `config/credentials.json`

### 2. Environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Anthropic API key
```

### 3. First Run (OAuth)

The first run opens a browser for Google OAuth consent:

```bash
python main.py --mode once
```

This saves a token to `config/token.json` for subsequent runs.

## Usage

### One-Shot Mode

Process any recent meetings and exit:

```bash
python main.py --mode once
```

### Polling Mode

Run continuously, checking for new meetings every N minutes:

```bash
python main.py --mode poll --interval 15
```

### Map Meetings to Documents

Tell the agent which Google Doc to append notes to for a recurring meeting:

```bash
python main.py --map "Weekly Standup" "1BxiMVs0XRA5nFMdKvBdBZjgmUii7OkVMhNSGht"
python main.py --map "Sprint Planning" "1abc123def456ghi789jkl012mno345pqr678stu"
```

The mapping uses partial matching, so `"Standup"` will match `"Weekly Standup - Engineering"`.

### Mapping File

Mappings are stored in `config/meeting_doc_map.json`. You can also edit this file directly:

```json
{
  "Weekly Standup": "your-google-doc-id-here",
  "Sprint Planning": "another-doc-id-here",
  "1:1 with Manager": "yet-another-doc-id"
}
```

## Document Structure

Each meeting entry in the doc includes:

- **Header** - Meeting title, date, attendees
- **Summary** - 2-4 sentence overview
- **Key Topics** - Main discussion points
- **Action Items** - Checkboxes with owners
- **Decisions** - What was decided
- **Follow-ups** - Items needing future attention

## Configuration

See `.env.example` for all configuration options. Key settings:

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | Required |
| `MEETING_LOOKBACK_HOURS` | How far back to scan for meetings | 2 |
| `POLL_INTERVAL_MINUTES` | Polling frequency | 15 |
| `CLAUDE_MODEL` | Claude model for summarization | claude-sonnet-5 |
| `OUTPUT_FOLDER_ID` | Drive folder for new docs | Root |
| `GEMINI_NOTES_FOLDER_ID` | Where Gemini saves notes | All Drive |

---

# Release tickets skill

Ask Claude in one sentence, for example *"Create the release tickets for SGW
4.1.4 GA Dec 10"*, and it creates the Jira release epic and child tasks
(QE, Perf, Docs, Publish Builds, Web Updates, plus the full GA checklist for
major releases). It covers Couchbase Lite (Android, iOS, Java, C, .NET), CBL
Vector Search, CBL JS, CBL React Native, Sync Gateway and Edge Server.

It works through the Atlassian connector in Claude: no API keys, tokens or
bots.
* Ticket content comes only from `config/release_catalog.yaml`.
* Every detail in the prompt is checked against the prompt's own words.
* Nothing is created until you reply "yes" to the plan.
* Created issues are read back and checked against the plan.

- Skill: `.claude/skills/release-tickets/` (Claude Code picks it up automatically
  in this repo). For Claude on the web or desktop, run
  `python scripts/build_skill.py` and upload `dist/release-tickets.zip`.
- Preview a release offline: `python release_agent.py preview --product sgw --version 4.1.4 --ga 2026-12-10`
- Design, guarantees and rollout: [docs/release-ticket-agent.md](docs/release-ticket-agent.md)
- Tests: `pip install -r requirements-dev.txt && pytest`
