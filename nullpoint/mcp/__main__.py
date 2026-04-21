"""Entry point: `python -m nullpoint.mcp` runs the stdio MCP server."""
from nullpoint.mcp.server import run_stdio

if __name__ == "__main__":
    run_stdio()
