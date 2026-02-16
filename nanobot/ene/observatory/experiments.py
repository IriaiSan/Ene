"""A/B experiment engine for testing prompt variants, models, and configs.

Supports:
- Multiple variants per experiment (control + N variants)
- Assignment methods: random, round_robin, caller_sticky
- Automatic result tracking via the MetricsCollector
- Statistical comparison: mean, confidence intervals, winner detection
- Quality scoring via LLM-as-judge (optional per experiment)
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from math import sqrt
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore


@dataclass
class Variant:
    """A single experiment variant."""
    id: str                # "control", "variant_a", etc.
    name: str              # Human-readable name
    config: dict = field(default_factory=dict)  # Model, temperature, prompt overrides
    weight: float = 1.0    # Assignment weight (higher = more traffic)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "config": self.config, "weight": self.weight}


@dataclass
class Experiment:
    """An A/B experiment definition."""
    id: str
    name: str
    description: str = ""
    variants: list[Variant] = field(default_factory=list)
    status: str = "active"          # active | paused | completed
    target_calls: int = 100         # Auto-complete after this many calls
    assignment_method: str = "random"  # random | round_robin | caller_sticky
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    ended_at: str | None = None
    config: dict = field(default_factory=dict)  # Extra config (quality scoring, etc.)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "variants": [v.to_dict() for v in self.variants],
            "status": self.status,
            "target_calls": self.target_calls,
            "assignment_method": self.assignment_method,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "config": self.config,
        }


class ExperimentEngine:
    """Manages A/B experiments: creation, assignment, results.

    Usage:
        engine = ExperimentEngine(store)

        # Create experiment
        exp = engine.create_experiment(
            name="DeepSeek vs GPT-4o",
            variants=[
                Variant("control", "DeepSeek v3.2", {"model": "deepseek/deepseek-v3.2"}),
                Variant("gpt4o", "GPT-4o", {"model": "openai/gpt-4o"}),
            ],
            target_calls=100,
        )

        # Get variant for a specific caller
        variant = engine.get_assignment(exp.id, "discord:123456")

        # Use variant.config to modify LLM call, then record result
        engine.record_result(exp.id, variant.id, call_id=42, quality_score=4.2)
    """

    def __init__(self, store: "MetricsStore"):
        self._store = store
        self._round_robin_counters: dict[str, int] = {}  # experiment_id -> counter
        self._caller_assignments: dict[str, dict[str, str]] = {}  # exp_id -> {caller_id -> variant_id}

    def create_experiment(
        self,
        name: str,
        variants: list[Variant],
        *,
        description: str = "",
        target_calls: int = 100,
        assignment_method: str = "random",
        config: dict | None = None,
    ) -> Experiment:
        """Create and persist a new experiment."""
        exp_id = f"exp-{uuid.uuid4().hex[:8]}"

        if len(variants) < 2:
            raise ValueError("Experiment needs at least 2 variants")

        experiment = Experiment(
            id=exp_id,
            name=name,
            description=description,
            variants=variants,
            target_calls=target_calls,
            assignment_method=assignment_method,
            config=config or {},
        )

        self._store.create_experiment(experiment.to_dict())
        logger.info(f"Experiment created: {exp_id} '{name}' ({len(variants)} variants)")
        return experiment

    def get_assignment(self, experiment_id: str, caller_id: str) -> Variant | None:
        """Get the variant assignment for a caller in an experiment.

        Args:
            experiment_id: The experiment to assign in.
            caller_id: The caller (e.g., "discord:123456").

        Returns:
            The assigned Variant, or None if experiment not found/active.
        """
        exp_data = self._store.get_experiment(experiment_id)
        if not exp_data or exp_data["status"] != "active":
            return None

        variants = [
            Variant(
                id=v["id"],
                name=v.get("name", v["id"]),
                config=v.get("config", {}),
                weight=v.get("weight", 1.0),
            )
            for v in exp_data["variants"]
        ]
        if not variants:
            return None

        method = exp_data.get("assignment_method", "random")

        if method == "caller_sticky":
            return self._assign_sticky(experiment_id, caller_id, variants)
        elif method == "round_robin":
            return self._assign_round_robin(experiment_id, variants)
        else:  # random (weighted)
            return self._assign_random(variants)

    def _assign_random(self, variants: list[Variant]) -> Variant:
        """Weighted random assignment."""
        weights = [v.weight for v in variants]
        return random.choices(variants, weights=weights, k=1)[0]

    def _assign_round_robin(self, experiment_id: str, variants: list[Variant]) -> Variant:
        """Round-robin assignment."""
        counter = self._round_robin_counters.get(experiment_id, 0)
        variant = variants[counter % len(variants)]
        self._round_robin_counters[experiment_id] = counter + 1
        return variant

    def _assign_sticky(
        self, experiment_id: str, caller_id: str, variants: list[Variant]
    ) -> Variant:
        """Sticky assignment — same caller always gets same variant."""
        # Check in-memory cache first
        if experiment_id in self._caller_assignments:
            cached_vid = self._caller_assignments[experiment_id].get(caller_id)
            if cached_vid:
                for v in variants:
                    if v.id == cached_vid:
                        return v

        # Deterministic hash-based assignment (consistent even after restart)
        hash_input = f"{experiment_id}:{caller_id}"
        hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
        variant = variants[hash_val % len(variants)]

        # Cache it
        if experiment_id not in self._caller_assignments:
            self._caller_assignments[experiment_id] = {}
        self._caller_assignments[experiment_id][caller_id] = variant.id

        return variant

    def record_result(
        self,
        experiment_id: str,
        variant_id: str,
        call_id: int | None = None,
        quality_score: float | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Record an experiment result."""
        self._store.record_experiment_result(
            experiment_id=experiment_id,
            variant_id=variant_id,
            call_id=call_id,
            quality_score=quality_score,
            metadata=metadata,
        )

        # Check if experiment should auto-complete
        results = self._store.get_experiment_results(experiment_id)
        exp = self._store.get_experiment(experiment_id)
        if exp and results["total_calls"] >= exp.get("target_calls", 100):
            self._store.update_experiment_status(experiment_id, "completed")
            logger.info(f"Experiment {experiment_id} auto-completed ({results['total_calls']} calls)")

    def get_results(self, experiment_id: str) -> dict[str, Any]:
        """Get experiment results with statistical comparison."""
        exp = self._store.get_experiment(experiment_id)
        if not exp:
            return {"error": "experiment not found"}

        raw_results = self._store.get_experiment_results(experiment_id)
        variants = raw_results.get("variants", {})

        # Add statistical analysis
        analysis = self._analyze_variants(variants)

        return {
            "experiment": exp,
            "variants": variants,
            "analysis": analysis,
            "total_calls": raw_results["total_calls"],
        }

    def _analyze_variants(self, variants: dict[str, dict]) -> dict[str, Any]:
        """Statistical comparison between variants."""
        if len(variants) < 2:
            return {"status": "insufficient_variants"}

        vids = list(variants.keys())
        analysis: dict[str, Any] = {"comparisons": []}

        # Compare each pair
        for i in range(len(vids)):
            for j in range(i + 1, len(vids)):
                va, vb = variants[vids[i]], variants[vids[j]]
                comparison = {
                    "variant_a": vids[i],
                    "variant_b": vids[j],
                }

                # Cost comparison
                if va.get("calls", 0) > 0 and vb.get("calls", 0) > 0:
                    comparison["cost_diff"] = {
                        "a_avg": va["avg_cost"],
                        "b_avg": vb["avg_cost"],
                        "cheaper": vids[i] if va["avg_cost"] < vb["avg_cost"] else vids[j],
                        "savings_pct": abs(va["avg_cost"] - vb["avg_cost"]) / max(va["avg_cost"], vb["avg_cost"], 0.0001) * 100,
                    }

                # Latency comparison
                if va.get("calls", 0) > 0 and vb.get("calls", 0) > 0:
                    comparison["latency_diff"] = {
                        "a_avg": va["avg_latency"],
                        "b_avg": vb["avg_latency"],
                        "faster": vids[i] if va["avg_latency"] < vb["avg_latency"] else vids[j],
                    }

                # Quality comparison (if scored)
                qa = va.get("avg_quality", 0)
                qb = vb.get("avg_quality", 0)
                if qa > 0 and qb > 0:
                    comparison["quality_diff"] = {
                        "a_avg": qa,
                        "b_avg": qb,
                        "better": vids[i] if qa > qb else vids[j],
                    }

                analysis["comparisons"].append(comparison)

        # Overall winner heuristic
        if analysis["comparisons"]:
            scores = {vid: 0 for vid in vids}
            for comp in analysis["comparisons"]:
                if "cost_diff" in comp:
                    scores[comp["cost_diff"]["cheaper"]] += 1
                if "latency_diff" in comp:
                    scores[comp["latency_diff"]["faster"]] += 1
                if "quality_diff" in comp:
                    scores[comp["quality_diff"]["better"]] += 2  # Quality weighted higher

            best = max(scores, key=scores.get)
            analysis["suggested_winner"] = best
            analysis["confidence"] = "low" if max(scores.values()) <= 2 else "medium" if max(scores.values()) <= 4 else "high"

        return analysis

    def list_active(self) -> list[dict]:
        """List all active experiments."""
        return self._store.get_active_experiments()

    def pause_experiment(self, experiment_id: str) -> None:
        """Pause an active experiment."""
        self._store.update_experiment_status(experiment_id, "paused")

    def resume_experiment(self, experiment_id: str) -> None:
        """Resume a paused experiment."""
        self._store.update_experiment_status(experiment_id, "active")

    def complete_experiment(self, experiment_id: str) -> None:
        """Mark an experiment as completed."""
        self._store.update_experiment_status(experiment_id, "completed")
