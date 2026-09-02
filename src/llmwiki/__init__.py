"""llmwiki - a compiled research knowledge base.

The public surface is ``__version__`` plus the canonical tool functions in
:mod:`llmwiki.tools`.  Everything else is reached by full path (see the plan, 4.4).

``tools`` is deliberately *not* imported here: importing :mod:`llmwiki` must stay
free of side effects and must not pull in the adapter stack.  Use
``from llmwiki import tools``, which resolves the submodule lazily.
"""

__version__ = "0.9.0"

__all__ = ["__version__"]
