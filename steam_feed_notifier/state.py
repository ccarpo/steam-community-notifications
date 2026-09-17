import json
from pathlib import Path


class SeenState:
    def __init__(self, path: str, limit: int = 5000, achievement_limit: int = 500):
        self.path = Path(path).expanduser()
        self.limit = limit
        self.achievement_limit = achievement_limit
        self.ids: list[str] = []
        self.achievements: dict[str, list[str]] = {}

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.ids = list(data.get("seen", []))[-self.limit :]
            stored = data.get("achievements", {})
            if isinstance(stored, dict):
                self.achievements = {
                    str(key): [str(name) for name in names]
                    for key, names in stored.items()
                    if isinstance(names, list)
                }
                self.achievements = dict(
                    list(self.achievements.items())[-self.achievement_limit :]
                )
            else:
                self.achievements = {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.ids = []
            self.achievements = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"seen": self.ids, "achievements": self.achievements},
                indent=2,
            )
            + "\n"
        )

    def add(self, ids: list[str]) -> None:
        existing = set(self.ids)
        for event_id in ids:
            if event_id not in existing:
                self.ids.append(event_id)
                existing.add(event_id)
        self.ids = self.ids[-self.limit :]
        self._save()

    def unseen_achievements(self, key: str, names: list[str]) -> list[str]:
        seen = set(self.achievements.get(key, []))
        return [name for name in names if name not in seen]

    def add_achievements(self, key: str, names: list[str]) -> None:
        if not names:
            return
        existing = self.achievements.get(key, [])
        for name in names:
            if name not in existing:
                existing.append(name)
        self.achievements[key] = existing
        self.achievements = dict(list(self.achievements.items())[-self.achievement_limit :])
        self._save()
