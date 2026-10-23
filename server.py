from fastmcp import FastMCP

mcp = FastMCP("DevOps Assistant")


@mcp.tool
def health_check(name: str = "developer") -> str:
    """Verify that the DevOps MCP server is running."""
    return f"Hello, {name}! Your MCP server is working."


if __name__ == "__main__":
    mcp.run()
