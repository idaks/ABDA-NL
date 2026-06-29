"""Bridge to the ABDA engine.

ABDA's internal modules use bare imports (`from KnowledgeBase.X import
Y`) that assume the engine root is on `sys.path`. This module inserts
`app/abda/` onto the path once, then re-exports the engine symbols the
NL app depends on. Centralizing the path setup here means engine files
stay untouchedand the rest of the app can use imports like `from
app.abda_bridge import RuleCollection`.
"""
import sys
from pathlib import Path

_ABDA_ROOT = Path(__file__).resolve().parent / "abda"
if str(_ABDA_ROOT) not in sys.path:
    sys.path.insert(0, str(_ABDA_ROOT))

from Configuration import Configuration  # noqa: E402
from KnowledgeBase.RuleCollection import RuleCollection  # noqa: E402
from KnowledgeBase.StrictRule import StrictRule  # noqa: E402
from KnowledgeBase.DefeasibleRule import DefeasibleRule  # noqa: E402
from ArgumentationSystem.Argument import Argument  # noqa: E402
from ArgumentationSystem.ArgumentBuilder import (  # noqa: E402
    ArgumentConstructionError,
    build_arguments,
    build_attacks,
)
from ArgumentationSystem.ArgumentationGraph import ArgumentationGraph  # noqa: E402
from ArgumentationSystem.Attack import Attack  # noqa: E402


def init_engine() -> None:
    """Apply the fixed v1 engine configuration (weakest-link +
    democratic order).

    Must be called before `build_arguments` / `build_attacks` because
    the `Argument.__le__` and `DefeasibleRuleSet.__le__` operators
    read `Configuration` at comparison time.
    """
    Configuration.WeakestLink = True
    Configuration.DemocraticOrder = True
    Configuration.Verbose = False


__all__ = [
    "Argument",
    "ArgumentConstructionError",
    "ArgumentationGraph",
    "Attack",
    "Configuration",
    "DefeasibleRule",
    "RuleCollection",
    "StrictRule",
    "build_arguments",
    "build_attacks",
    "init_engine",
]
