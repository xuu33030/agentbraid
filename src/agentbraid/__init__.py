"""AgentBraid public package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentbraid")
except PackageNotFoundError:
    __version__ = "0.2.0a3"

__all__ = ["__version__"]
