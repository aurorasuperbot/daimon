"""Entry point: `python -m daimon.mcp` runs the stdio MCP server."""
from daimon.mcp.server import run_stdio

if __name__ == "__main__":
    run_stdio()
