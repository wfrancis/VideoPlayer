"""MCP server for controlling the local Video Player app.

Bridges Claude Code tool calls to the HTTP control API exposed by
the running Video Player app.
"""
import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BASE = os.environ.get("VIDEO_PLAYER_BASE_URL", "http://127.0.0.1:9876").rstrip("/")


def _post(path: str, data: dict | None = None) -> dict:
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        return {"error": f"Video Player not reachable at {BASE} — is the app running? ({e})"}


def _get(path: str) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=2) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        return {"error": f"Video Player not reachable at {BASE} — is the app running? ({e})"}


app = Server("video-player")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="health", description="Check whether the local Video Player app is reachable",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="state", description="Get current player state (position, duration, playing, rate, file, etc.)",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="play", description="Start playback",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="pause", description="Pause playback",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="toggle", description="Toggle play/pause",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="skip", description="Skip N seconds from current position (negative = back)",
             inputSchema={
                 "type": "object",
                 "properties": {"seconds": {"type": "number"}},
                 "required": ["seconds"],
             }),
        Tool(name="seek", description="Seek to an absolute position in seconds",
             inputSchema={
                 "type": "object",
                 "properties": {"seconds": {"type": "number"}},
                 "required": ["seconds"],
             }),
        Tool(name="set_speed", description="Set playback rate (e.g. 0.25, 0.5, 1, 2, 3)",
             inputSchema={
                 "type": "object",
                 "properties": {"rate": {"type": "number"}},
                 "required": ["rate"],
             }),
        Tool(name="set_volume", description="Set volume 0.0–1.0",
             inputSchema={
                 "type": "object",
                 "properties": {"level": {"type": "number"}},
                 "required": ["level"],
             }),
        Tool(name="mute", description="Mute or unmute",
             inputSchema={
                 "type": "object",
                 "properties": {"on": {"type": "boolean"}},
                 "required": ["on"],
             }),
        Tool(name="zoom", description="Zoom by a factor (>1 zoom in, <1 zoom out)",
             inputSchema={
                 "type": "object",
                 "properties": {"factor": {"type": "number"}},
                 "required": ["factor"],
             }),
        Tool(name="reset_zoom", description="Reset zoom to fit",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="open", description="Open a video file by absolute path",
             inputSchema={
                 "type": "object",
                 "properties": {"path": {"type": "string"}},
                 "required": ["path"],
             }),
        Tool(name="set_pip", description="Enable or disable the floating mini-player mode",
             inputSchema={
                 "type": "object",
                 "properties": {"on": {"type": "boolean"}},
                 "required": ["on"],
             }),
        Tool(name="set_view_mode", description="Set video framing mode: fit, fill, or stretch",
             inputSchema={
                 "type": "object",
                 "properties": {"mode": {"type": "string", "enum": ["fit", "fill", "stretch"]}},
                 "required": ["mode"],
             }),
        Tool(name="cycle_view_mode", description="Cycle between fit, fill, and stretch modes",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="set_fullscreen", description="Enable or disable fullscreen mode",
             inputSchema={
                 "type": "object",
                 "properties": {"on": {"type": "boolean"}},
                 "required": ["on"],
             }),
        Tool(name="open_subtitles", description="Load an external .srt subtitle file",
             inputSchema={
                 "type": "object",
                 "properties": {"path": {"type": "string"}},
                 "required": ["path"],
             }),
        Tool(name="toggle_subtitles", description="Toggle subtitles on or off",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="set_subtitles_enabled", description="Enable or disable subtitles",
             inputSchema={
                 "type": "object",
                 "properties": {"on": {"type": "boolean"}},
                 "required": ["on"],
             }),
        Tool(name="set_subtitle_delay", description="Set subtitle delay in milliseconds",
             inputSchema={
                 "type": "object",
                 "properties": {"ms": {"type": "integer"}},
                 "required": ["ms"],
             }),
        Tool(name="set_subtitle_size", description="Set subtitle font size in pixels",
             inputSchema={
                 "type": "object",
                 "properties": {"size": {"type": "integer"}},
                 "required": ["size"],
             }),
        Tool(name="set_subtitle_color", description="Set subtitle color name",
             inputSchema={
                 "type": "object",
                 "properties": {"name": {"type": "string"}},
                 "required": ["name"],
             }),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    args = arguments or {}
    if name == "health":
        result = _get("/health")
    elif name == "state":
        result = _get("/state")
    elif name == "play":
        result = _post("/play")
    elif name == "pause":
        result = _post("/pause")
    elif name == "toggle":
        result = _post("/toggle")
    elif name == "skip":
        result = _post("/skip", {"seconds": args["seconds"]})
    elif name == "seek":
        result = _post("/seek", {"seconds": args["seconds"]})
    elif name == "set_speed":
        result = _post("/speed", {"rate": args["rate"]})
    elif name == "set_volume":
        result = _post("/volume", {"level": args["level"]})
    elif name == "mute":
        result = _post("/mute", {"on": args["on"]})
    elif name == "zoom":
        result = _post("/zoom", {"factor": args["factor"]})
    elif name == "reset_zoom":
        result = _post("/reset_zoom")
    elif name == "open":
        result = _post("/open", {"path": args["path"]})
    elif name == "set_pip":
        result = _post("/pip", {"on": args["on"]})
    elif name == "set_view_mode":
        result = _post("/view_mode", {"mode": args["mode"]})
    elif name == "cycle_view_mode":
        result = _post("/cycle_view_mode")
    elif name == "set_fullscreen":
        result = _post("/fullscreen", {"on": args["on"]})
    elif name == "open_subtitles":
        result = _post("/subtitles/open", {"path": args["path"]})
    elif name == "toggle_subtitles":
        result = _post("/subtitles/toggle")
    elif name == "set_subtitles_enabled":
        result = _post("/subtitles/enabled", {"on": args["on"]})
    elif name == "set_subtitle_delay":
        result = _post("/subtitles/delay", {"ms": args["ms"]})
    elif name == "set_subtitle_size":
        result = _post("/subtitles/size", {"size": args["size"]})
    elif name == "set_subtitle_color":
        result = _post("/subtitles/color", {"name": args["name"]})
    else:
        result = {"error": f"unknown tool: {name}"}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
