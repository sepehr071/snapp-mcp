"""Unofficial MCP server for Snappfood and SnappMarket."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("snapp-mcp")
except PackageNotFoundError:  # imported from a source checkout without installing
    __version__ = "0.0.0.dev0"
