"""Ask Sillage: the in-app AI assistant. A streaming Claude agent loop over the shared tool registry.

Events streamed to the browser (one JSON object per SSE `data:` line):
    {"type": "text", "delta": "..."}                       answer text as it is written
    {"type": "tool", "id", "name", "input"}                Claude called a tool
    {"type": "tool_result", "id", "name", "ok", "preview"} it finished
    {"type": "done"} | {"type": "error", "message"}
"""

from __future__ import annotations

import json
from datetime import date
from typing import Iterator

from .claude_client import FALLBACK_BETA, MODEL, client
from . import tools

MAX_ROUNDS = 12
MAX_RESULT_CHARS = 24_000

SYSTEM = """You are Sillage, the AI inside a fragrance collector's personal app. You have live tools over their
collection (Fragrantica notes and accords, their ratings from -2 hate to +2 love, and their wear log) and a learned
taste model.

How to work:
- Ground every answer in their data: call tools rather than guessing. collection_overview orients you; taste_profile
  holds the learned likes and dislikes; query_database runs read-only SQL for anything custom; evaluate_fragrance
  checks a fragrance they don't own against their taste.
- Be a knowledgeable friend at a perfume counter: specific, warm, concise. Name actual fragrances and notes.
- Use Markdown: short paragraphs, bullet lists, bold for fragrance names. Refer to fragrances by name, never by id.
- Ask before changing data (add_fragrance, update_fragrance, log_wear) unless they clearly asked for the change.
- publish_insight puts a finding (with an optional chart) in the app's Insights feed. Use it when they ask to save
  or pin something, or after a substantial analysis they asked for; say that you did.
Today is {today}."""


def _preview(result) -> str:
    text = json.dumps(result, default=str)
    return text[:160] + ("…" if len(text) > 160 else "")


def run(history: list[dict]) -> Iterator[dict]:
    import anthropic

    messages = [{"role": m["role"], "content": m["content"]} for m in history if m.get("content")]
    tool_defs = tools.claude_tools()
    system = SYSTEM.format(today=date.today().isoformat())
    for _ in range(MAX_ROUNDS):
        try:
            with client().beta.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=tool_defs,
                messages=messages,
                output_config={"effort": "medium"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "delta": event.text}
                final = stream.get_final_message()
        except ValueError:
            # Unparseable streamed tool input: there is no tool_use block to answer, so ask again once.
            yield {"type": "error", "message": "The assistant produced a malformed tool call. Please ask again."}
            return
        except anthropic.AuthenticationError:
            yield {"type": "error", "message": "The Anthropic API key was rejected."}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "Rate limited by the Anthropic API. Try again in a minute."}
            return
        except anthropic.APIStatusError as exc:
            yield {"type": "error", "message": f"Anthropic API error {exc.status_code}: {exc.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Couldn't reach the Anthropic API."}
            return

        messages.append({"role": "assistant", "content": final.content})
        if final.stop_reason == "refusal":
            yield {"type": "error", "message": "The assistant declined this request."}
            return
        uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason == "max_tokens":
            if uses:  # a truncated tool input may parse as a valid partial object: never run it
                yield {"type": "error", "message": "The answer got too long. Try a narrower question."}
            return
        if final.stop_reason == "pause_turn":
            continue
        if not uses:
            yield {"type": "done"}
            return

        results = []
        for block in uses:
            yield {"type": "tool", "id": block.id, "name": block.name, "input": block.input}
            if not isinstance(block.input, dict):
                ok, result = False, {"INVALID_JSON": str(block.input)}
            else:
                ok, result = tools.call_tool(block.name, block.input, caller="Ask Sillage")
            content = json.dumps(result, default=str)
            if len(content) > MAX_RESULT_CHARS:
                content = content[:MAX_RESULT_CHARS] + ' …(truncated; narrow the query)"'
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": not ok})
            yield {"type": "tool_result", "id": block.id, "name": block.name, "ok": ok, "preview": _preview(result)}
        messages.append({"role": "user", "content": results})
    yield {"type": "error", "message": "Stopped after too many steps. Try a more specific question."}
