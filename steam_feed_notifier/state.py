import json
from pathlib import Path


class SeenState:
    def __init__(self, path: str, limit: int = 5000):
        self.path = Path(path).expanduser()
        self.limit = limit
        self.ids: list[str] = []

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.ids = list(data.get("seen", []))[-self.limit :]
        except (FileNotFoundError, json.JSONDecodeError):
            self.ids = []

    def add(self, ids: list[str]) -> None:
        existing = set(self.ids)
        for event_id in ids:
            if event_id not in existing:
                self.ids.append(event_id)
                existing.add(event_id)
        self.ids = self.ids[-self.limit :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": self.ids}, indent=2) + "\n")
