import os
import re

import yaml

CATALOG_FILE = os.getenv("RELEASE_CATALOG_PATH", "config/release_catalog.yaml")


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

    def resolve_product(self, name: str) -> str:
        """Map a key, title or alias ('Sync Gateway', 'sgw', 'CBL JS') to a product key."""
        needle = name.strip().lower()
        for key, product in self.products.items():
            names = {key, product["title"].lower(), product["name"].lower()}
            names.update(a.lower() for a in product.get("aliases", []))
            if needle in names:
                return key
        raise KeyError(f"Unknown product '{name}'. Known: {', '.join(self.products)}")

    def product_for_summary(self, summary: str) -> str | None:
        for key, product in self.products.items():
            if re.search(product["match"], summary, re.IGNORECASE):
                return key
        return None

    def scope_in_summary(self, product_key: str, summary: str) -> tuple[str, ...]:
        """Platforms named in a title like 'CBL .NET ... 4.1.1'; () means the full product."""
        platforms = self.products[product_key].get("platforms", [])
        if len(platforms) < 2:
            return ()
        return tuple(sorted(
            p for p in platforms
            if re.search(rf"(?<!\w){re.escape(self.platforms[p])}(?!\w)", summary, re.IGNORECASE)
        ))

    def owner_for(self, product_key: str, function: str) -> str | None:
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
