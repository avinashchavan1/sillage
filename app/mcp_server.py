"""Sillage as an MCP server.

Remote (Streamable HTTP, mounted by app.main at /mcp — needs the API key):
    claude mcp add --transport http sillage http://127.0.0.1:8000/mcp --header "Authorization: Bearer <key>"
Local (stdio, for Claude Desktop and other desktop clients; no key, reads the same database):
    python -m app.mcp_server
"""

from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import db, tools

INSTRUCTIONS = """Sillage is this person's fragrance collection: every perfume they own, sampled, tried, sold or
want, with Fragrantica notes and accords, their own ratings (-2 hate … +2 love) and a wear log.
Start with collection_overview. For taste questions use taste_profile; for anything custom use query_database
(read-only SQL, schema in its description). evaluate_fragrance answers 'would I like X?' for fragrances they
don't own. When you produce an analysis worth keeping, or the person asks to save it, call publish_insight so it
appears in their Sillage app — include a chart when numbers are involved. Ask before add/update/log tools unless
the person clearly asked for that change."""


def build_server() -> MCPServer:
    server = MCPServer("Sillage", title="Sillage fragrance collection", instructions=INSTRUCTIONS, version="2.0")
    for spec in tools.REGISTRY.values():
        server.add_tool(
            _with_caller(spec.fn),
            name=spec.name,
            description=spec.description,
            annotations=ToolAnnotations(readOnlyHint=spec.read_only, destructiveHint=False,
                                        idempotentHint=spec.read_only, openWorldHint=spec.name in ("evaluate_fragrance", "add_fragrance", "similar_fragrances")),
        )

    @server.resource("sillage://schema", name="database-schema", description="Tables and views for query_database", mime_type="text/plain")
    def schema() -> str:
        return db.SQL_GUIDE

    @server.resource("sillage://taste-profile", name="taste-profile", description="The learned taste model", mime_type="application/json")
    def profile() -> str:
        return json.dumps(tools.taste_profile(), default=str)

    @server.prompt(name="analyze-my-taste", description="Deep-dive on what this person loves and avoids, saved to the app")
    def analyze() -> str:
        return ("Analyse my fragrance taste using Sillage. Look at the taste profile and run any SQL you need. Tell me what "
                "I gravitate to and avoid, any surprising patterns, and 5 fragrances I don't own that I'd likely love "
                "(check each with evaluate_fragrance). Publish the key finding to my Insights feed with a chart.")

    @server.prompt(name="what-to-wear", description="Pick a fragrance for right now")
    def wear(weather: str = "", occasion: str = "") -> str:
        return (f"Help me choose a fragrance to wear today. Weather: {weather or 'ask me'}. Occasion: {occasion or 'ask me'}. "
                "Use suggest_what_to_wear and explain the picks briefly.")

    return server


def _with_caller(fn):
    """Tag writes made over MCP so published insights show where they came from."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = tools.CALLER.set("MCP client")
        try:
            return fn(*args, **kwargs)
        except tools.ToolError as exc:
            raise ValueError(str(exc)) from exc  # surfaces to the client as a tool error
        finally:
            tools.CALLER.reset(token)

    return wrapper


def http_app(server: MCPServer):
    hosts = [h.strip() for h in os.environ.get("SILLAGE_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "127.0.0.1", "localhost", "testserver", *hosts],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", *[f"https://{h}" for h in hosts]],
    )
    return server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                      transport_security=security)


if __name__ == "__main__":
    build_server().run("stdio")
