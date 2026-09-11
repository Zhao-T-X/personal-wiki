"""Context Runtime: plan, budget, compile and trace the context of every LLM call."""
from .budget import AGENT_BUDGETS, DEFAULT_BUDGET, NEVER_TRIM, budget_for
from .compiler import CompiledContext, ContextCompiler
from .history import compress_history
from .items import ContextItem, Section
from .manager import AgentContext, ContextManager
from .packet import TaskPacket, get_packet, list_packets, save_packet
from .planner import ContextPlan, ContextPlanner, SectionPlan
from .policies import Load
from .tokens import estimate_tokens
from .trace import get_context_run, list_context_runs, record_context
from .value import rank, score, utility, utility_per_1k

# Shared runtime singletons: providers are stateless, so one registry serves all
# callers and keeps `compose_prompt` free of per-call wiring.
def default_registry():
    """The provider registry used by the default planner/compiler pair."""
    from .providers import HistoryProvider, ProviderRegistry, SkillProvider, ToolProvider
    registry = ProviderRegistry()
    registry.register(SkillProvider())
    registry.register(ToolProvider())
    registry.register(HistoryProvider())
    return registry


PLANNER = ContextPlanner()
COMPILER = ContextCompiler()
REGISTRY = default_registry()

__all__ = [
    'AGENT_BUDGETS', 'DEFAULT_BUDGET', 'NEVER_TRIM', 'budget_for',
    'AgentContext', 'ContextItem', 'ContextManager', 'Section', 'Load', 'estimate_tokens',
    'compress_history',
    'ContextPlan', 'ContextPlanner', 'SectionPlan',
    'CompiledContext', 'ContextCompiler',
    'TaskPacket', 'save_packet', 'get_packet', 'list_packets',
    'record_context', 'list_context_runs', 'get_context_run',
    'rank', 'score', 'utility', 'utility_per_1k',
    'PLANNER', 'COMPILER', 'REGISTRY', 'default_registry',
]
