"""Answer cache and persistent limits ledger (ADR-001 §5, ADR-004 §8).

The cache is read only in modes ``cache`` and ``live`` (the cascade decides); it stores the
unchanged body of HTTP 200 responses, never image bytes or headers. The ledger counts every
attempt before its request is sent and is cumulative per ``experiment_id`` (TAC-001-04).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from map_qa.models import MapQaError, TierConfig

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class AnswerCache:
    """Cache entry: ``<root>/answers/<model with / → __>/<question_version>/<image_sha256>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path(self, model: str, question_version: str, image_sha256: str) -> Path:
        model_dir = model.replace("/", "__")
        if not _SHA_RE.match(image_sha256):
            raise ValueError("image_sha256 must be 64 lowercase hex characters")
        for segment in (model_dir, question_version):
            if not _SEGMENT_RE.match(segment) or segment in (".", ".."):
                raise ValueError(f"invalid cache key segment: {segment!r}")
        return self.root / "answers" / model_dir / question_version / f"{image_sha256}.json"

    def get(self, model: str, question_version: str, image_sha256: str) -> dict | None:
        path = self.path(model, question_version, image_sha256)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, model: str, question_version: str, image_sha256: str, entry: dict) -> Path:
        path = self.path(model, question_version, image_sha256)
        data = dict(entry)
        data["key"] = {"image_sha256": image_sha256, "model": model, "question_version": question_version}
        _write_json_atomic(path, data)
        return path


class Ledger:
    """Persistent attempt and cost counters per experiment and tier (AC-15)."""

    def __init__(self, path: Path, experiment_id: str, tiers: dict[str, TierConfig]) -> None:
        self.path = Path(path)
        self.experiment_id = experiment_id
        self.tiers = tiers
        self._data: dict[str, Any] = {"schema_version": 1, "experiments": {}}
        if self.path.is_file():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        experiment = self._data["experiments"].setdefault(experiment_id, {})
        for name, config in tiers.items():
            state = experiment.get(name)
            if state is None:
                experiment[name] = {"model": config.model, "attempts": 0, "cost_usd": 0.0, "limit_reached": None}
            elif state["model"] != config.model and state["attempts"] > 0:
                raise MapQaError(
                    f"ledger: experiment '{experiment_id}' already used model '{state['model']}' as {name}; "
                    f"use a new --experiment-id for '{config.model}'"
                )
            else:
                state["model"] = config.model

    def _state(self, tier: str) -> dict[str, Any]:
        return self._data["experiments"][self.experiment_id][tier]

    def attempts(self, tier: str) -> int:
        return int(self._state(tier)["attempts"])

    def cost(self, tier: str) -> float:
        return float(self._state(tier)["cost_usd"])

    def blocked(self, tier: str) -> str | None:
        """Why the next call of this tier may not be sent: ``max_requests``, ``max_cost_usd`` or None."""
        config = self.tiers[tier]
        if self.attempts(tier) >= config.max_requests:
            return "max_requests"
        if self.cost(tier) + config.reservation_usd > config.max_cost_usd + 1e-12:
            return "max_cost_usd"
        return None

    def reserve(self, tier: str) -> float:
        """Count the attempt and reserve its cost; written to disk before the request is sent."""
        state = self._state(tier)
        reservation = self.tiers[tier].reservation_usd
        state["attempts"] += 1
        state["cost_usd"] = round(state["cost_usd"] + reservation, 10)
        self.save()
        return reservation

    def settle(self, tier: str, reserved: float, actual_cost: float | None) -> bool:
        """Replace the reservation with ``usage.cost``; True when the actual cost exceeded it."""
        state = self._state(tier)
        exceeded = False
        if actual_cost is not None:
            state["cost_usd"] = round(state["cost_usd"] - reserved + actual_cost, 10)
            exceeded = actual_cost > reserved
        self.save()
        return exceeded

    def mark_limit(self, tier: str, reason: str) -> None:
        self._state(tier)["limit_reached"] = reason
        self.save()

    def save(self) -> None:
        _write_json_atomic(self.path, self._data)
