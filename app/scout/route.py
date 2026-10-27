"""Route and Recommend: capability graph to tool plan, model profile choice."""

from __future__ import annotations

from typing import Any


class RouteError(Exception):
    """A capability has no usable tool. Scout never substitutes an out-of-scope tool."""

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(f"{capability}: {reason}")
        self.capability, self.reason = capability, reason


class Graph:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.tools: dict[str, dict[str, Any]] = spec["tools"]
        self.caps: dict[str, list[str]] = spec["capabilities"]
        self.budget: dict[str, int] = spec.get("latency_budget_ms", {})
        self.profiles: dict[str, dict[str, Any]] = spec.get("model_profiles", {})
        self.tasks: dict[str, list[str]] = spec.get("tasks", {})

    def pick(self, capability: str, *, surface: str, permissions: set[str], availability: dict[str, bool]) -> str:
        """Spec 7 selection: permissions, availability, latency budget, cost, then local-first, then graph order."""
        if capability not in self.caps:
            raise RouteError(capability, "no tool registered")
        order = self.caps[capability]
        options = [t for t in order if set(self.tools[t].get("permissions", [])) <= permissions]
        if not options:
            raise RouteError(capability, "no permitted tool")
        options = [t for t in options if availability.get(t, True)]
        if not options:
            raise RouteError(capability, "every tool is unavailable")
        budget = self.budget.get(surface)
        fast = [t for t in options if budget is None or self.tools[t].get("latency_ms", 0) <= budget] or options
        return min(fast, key=lambda t: (self.tools[t].get("cost", 0), not self.tools[t].get("local", False), order.index(t)))

    def plan(self, capabilities: list[str], *, surface: str, permissions: set[str], availability: dict[str, bool],
             inputs: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"capability": c, "tool": self.pick(c, surface=surface, permissions=permissions, availability=availability),
                 "input": inputs} for c in capabilities]

    def model(self, task: str, *, data_class: str, availability: dict[str, bool]) -> dict[str, Any]:
        """First profile cleared for the data class and available; local profiles take sensitive classes."""
        for name in self.tasks.get(task, []):
            prof = self.profiles[name]
            if data_class in prof.get("data_classes", []) and availability.get(name, True):
                return {"task": task, "profile": name, "provider": prof["provider"], "local": prof.get("local", False)}
        raise RouteError(task, f"no model profile cleared for {data_class}")
