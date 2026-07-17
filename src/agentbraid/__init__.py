"""AgentBraid public package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentbraid")
except PackageNotFoundError:
    __version__ = "0.1.0a2"

__all__ = ["__version__"]
