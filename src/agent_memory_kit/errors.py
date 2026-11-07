from fastmcp.exceptions import ToolError


class PublicError(ToolError):
    """Safe, stable error shown to MCP callers without internal details."""
