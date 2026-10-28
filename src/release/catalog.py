import os
import re

import yaml

CATALOG_FILE = os.getenv("RELEASE_CATALOG_PATH", "config/release_catalog.yaml")

JIRA_KEYS = ("project", "epic_issue_type", "task_issue_type")
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _mention_re(term: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.]){re.escape(term)}(?![\w])", re.IGNORECASE)


class Catalog:
    """Typed access to config/release_catalog.yaml."""

    def __init__(self, data: dict):
        self.data = data
        self.jira = data["jira"]
        self.people = data.get("people", {})
        self.platforms = data.get("platforms", {})
        self.products = data["products"]
        self.tasks = data["tasks"]
        self._validate()

    @classmethod
    def load(cls, path: str = CATALOG_FILE) -> "Catalog":
        with open(path) as f:
            return cls(yaml.safe_load(f))

    def _validate(self) -> None:
        for key, product in self.products.items():
            for release_type, task_ids in product["tasks"].items():
                for task_id in task_ids:
                    if task_id not in self.tasks:
                        raise ValueError(f"{key}/{release_type}: unknown task '{task_id}'")
            for platform in product.get("platforms", []):
                if platform not in self.platforms:
                    raise ValueError(f"{key}: unknown platform '{platform}'")
            unknown = set(product.get("jira", {})) - set(JIRA_KEYS)
            if unknown:
                raise ValueError(f"{key}: unsupported jira settings {sorted(unknown)}")
            if not PROJECT_KEY_RE.match(self.jira_for(key)["project"]):
                raise ValueError(f"{key}: invalid Jira project key '{self.jira_for(key)['project']}'")
        people = [("people", f, p) for f, p in self.people.items()]
        people += [(k, f, p) for k, prod in self.products.items() for f, p in prod.get("owners", {}).items()]
        for where, function, person in people:
            if person is not None and not (isinstance(person, dict) and person.get("account_id") and person.get("name")):
                raise ValueError(f"{where}/{function}: owner must be null or {{account_id, name}}")
        for task_id, task in self.tasks.items():
            if task["owner"] not in self.people:
                raise ValueError(f"task '{task_id}': unknown owner function '{task['owner']}'")
            for sections in (task.get("sections") or {}).values():
                for section in sections:
                    for row in section.get("rows", []):
                        # An unquoted comma in a YAML flow mapping silently splits the row text.
                        if isinstance(row, dict) and (not isinstance(row.get("text"), str)
                                                      or None in row.values()):
                            raise ValueError(f"task '{task_id}': malformed row {row!r} (quote the text)")

    def jira_for(self, product_key: str) -> dict:
        """Jira settings for a product: catalog defaults overlaid with the product's own `jira:` block."""
        settings = {k: self.jira[k] for k in (*JIRA_KEYS, "agent_label")}
        settings.update(self.products[product_key].get("jira", {}))
        return settings

    def projects(self) -> dict[str, list[str]]:
        """Jira project key -> products filed there."""
        out: dict[str, list[str]] = {}
        for key in self.products:
            out.setdefault(self.jira_for(key)["project"], []).append(key)
        return out

    def product_names(self, product_key: str) -> set[str]:
        product = self.products[product_key]
        names = {product_key, product["title"], product["name"], *product.get("aliases", [])}
        return {n.lower() for n in names}

    def resolve_product(self, name: str) -> str:
        """Map a key, title or alias ('Sync Gateway', 'sgw', 'CBL JS') to a product key."""
        needle = name.strip().lower()
        for key in self.products:
            if needle in self.product_names(key):
                return key
        raise KeyError(f"Unknown product '{name}'. Known: {', '.join(self.products)}")

    def product_mentioned_before(self, text: str, position: int) -> str | None:
        """The product whose name ends closest before `position` in `text` (longest name wins ties).

        Used to check which product a version number belongs to, e.g. in
        "CBL 4.1.4 and CBL JS 1.0.4" the 1.0.4 belongs to CBL JS, not CBL, and in
        "Couchbase Lite for JavaScript 1.0.4" the full name beats its "lite" suffix.
        """
        best: tuple[int, int, str] | None = None
        for key in self.products:
            for name in self.product_names(key):
                for m in _mention_re(name).finditer(text, 0, position):
                    candidate = (m.end(), len(name), key)
                    if best is None or candidate[:2] > best[:2]:
                        best = candidate
        return best[2] if best else None

    def platforms_mentioned(self, product_key: str, text: str) -> set[str]:
        """Platforms of this product named in `text` (by display name or key)."""
        found = set()
        for p in self.products[product_key].get("platforms", []):
            terms = {self.platforms[p], p, p.replace("_", " ")}
            if any(_mention_re(t).search(text) for t in terms):
                found.add(p)
        return found

    def product_for_summary(self, summary: str) -> str | None:
        for key, product in self.products.items():
            if re.search(product["match"], summary, re.IGNORECASE):
                return key
        return None

    def scope_in_summary(self, product_key: str, summary: str) -> tuple[str, ...]:
        """Platforms named in a title like 'CBL .NET ... 4.1.1'; () means the full product."""
        if len(self.products[product_key].get("platforms", [])) < 2:
            return ()
        return tuple(sorted(self.platforms_mentioned(product_key, summary)))

    def owner_for(self, product_key: str, function: str) -> dict | None:
        """{account_id, name} of whoever owns `function` for this product, or None if unassigned."""
        overrides = self.products[product_key].get("owners", {})
        if function in overrides:
            return overrides[function]
        return self.people.get(function)

    def task_ids_for(self, product_key: str, release_type: str) -> list[str]:
        return list(self.products[product_key]["tasks"][release_type])

    def sections_for(self, task_id: str, product_key: str, release_type: str) -> list[dict]:
        sections = self.tasks[task_id].get("sections") or {}
        for key in (product_key, release_type, "default"):
            if key in sections:
                return sections[key]
        return []
