class FakeJira:
    """In-memory stand-in for JiraClient that understands the JQL the planner sends."""

    def __init__(self, issues=None, fail_on_create=None):
        self.issues = issues or []  # dicts: key, summary, type, labels, parent, project
        self.created = []
        self.comments = {}
        self.fail_on_create = fail_on_create  # raise on the Nth create (1-based)

    def search(self, jql, fields):
        if jql.startswith("parent = "):
            parent = jql.split("=", 1)[1].strip()
            hits = [i for i in self.issues if i.get("parent") == parent]
        else:
            project = jql.split("project = ", 1)[1].split()[0]
            epics = [i for i in self.issues if i["type"] == "Epic" and i.get("project", "CM") == project]
            if "labels =" in jql:
                label = jql.split('labels = "', 1)[1].split('"', 1)[0]
                hits = [i for i in epics if label in i.get("labels", [])]
            else:
                hits = [i for i in epics if "Release Ticket" in i["summary"]]
        return [{"key": i["key"], "fields": {"summary": i["summary"], "duedate": None}} for i in hits]

    def create_issue(self, project, issue_type, summary, description, labels,
                     assignee_id=None, due_date=None, parent_key=None):
        if self.fail_on_create and len(self.created) + 1 == self.fail_on_create:
            raise RuntimeError("Jira 500")
        key = f"{project}-{9000 + len(self.created)}"
        self.issues.append({"key": key, "summary": summary, "type": issue_type, "labels": labels,
                            "parent": parent_key, "project": project, "assignee": assignee_id})
        self.created.append(key)
        return key

    def add_comment(self, key, body):
        self.comments.setdefault(key, []).append(body)

    def display_name(self, account_id):
        return f"name-of-{account_id}"

    def browse_url(self, key):
        return f"https://jira.example/browse/{key}"


def create_from_plan(plan, jira: FakeJira) -> list[str]:
    """Create a plan's missing issues the way the skill instructs Claude to: epic first, then tasks."""
    if plan.blocked:
        raise RuntimeError("Plan is blocked")
    created = []
    epic = plan.epic
    if epic.is_new:
        epic.existing_key = jira.create_issue(epic.project, "Epic", epic.summary, epic.description,
                                              epic.labels, epic.assignee_id, epic.due_date)
        created.append(epic.existing_key)
    for task in plan.tasks:
        if task.is_new:
            task.existing_key = jira.create_issue(task.project, "Task", task.summary, task.description,
                                                  task.labels, task.assignee_id, task.due_date,
                                                  parent_key=epic.existing_key)
            created.append(task.existing_key)
    return created
