"""Local Browser Agent: structured, safety-controlled browser automation."""

from .actions import Action, Expectation, Plan, PlanStep
from .agent import Agent, AgentConfig, RunResult
from .controller import BrowserController
from .planner import OllamaPlanner, RulesPlanner, build_planner
from .safety import ConfirmRequest, SafetyPolicy, approve_all, deny_all, interactive_confirmer

__version__ = "0.1.0"

__all__ = [
    "Action", "Expectation", "Plan", "PlanStep", "Agent", "AgentConfig", "RunResult", "BrowserController",
    "RulesPlanner", "OllamaPlanner", "build_planner", "SafetyPolicy", "ConfirmRequest", "approve_all",
    "deny_all", "interactive_confirmer", "__version__",
]
