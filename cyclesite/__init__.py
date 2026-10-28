"""CycleSite Copilot core library: deterministic site-selection logic.

Everything numeric lives here (not in the LLM): scoring, optimisation, backtesting,
transfer checks, freshness and SQL guardrails. The ADK agent in ``cycle_agent`` only
orchestrates these functions and explains their output.
"""

__version__ = "0.1.0"
