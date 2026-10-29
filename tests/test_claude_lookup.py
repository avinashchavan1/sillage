from types import SimpleNamespace

import pytest

from app.lookup import claude as claude_provider

SUBMITTED = {
    "name": "Aventus", "brand": "Creed", "year": 2010, "concentration": "Eau de Parfum", "gender": "Men",
    "family": "Chypre Fruity", "perfumers": ["Jean-Christophe Hérault", "Erwin Creed"],
    "accords": [{"name": "fruity", "strength": 1.0}, {"name": "woody", "strength": 0.8}],
    "top_notes": ["Pineapple", "Bergamot"], "middle_notes": ["Birch", "Patchouli"], "base_notes": ["Musk"],
    "notes": [], "description": "Smoky pineapple.", "longevity": "long lasting", "sillage": "strong",
    "seasons": ["spring", "summer"], "community_rating": 4.3, "rating_votes": 20000,
    "source_url": "https://www.fragrantica.com/perfume/Creed/Aventus-9828.html",
    "match_note": "Read 'aventis' as Creed Aventus", "alternatives": [
        {"label": "Creed – Aventus Absolu", "url": "https://www.fragrantica.com/perfume/Creed/Aventus-Absolu-1.html"}],
}


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def fake_client(monkeypatch, responses):
    messages = FakeMessages(responses)
    monkeypatch.setattr(claude_provider, "client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def block(**kw):
    return SimpleNamespace(**kw)


def test_pause_turn_then_submit(monkeypatch):
    messages = fake_client(monkeypatch, [
        SimpleNamespace(stop_reason="pause_turn", content=[block(type="server_tool_use", name="web_search")]),
        SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", name="submit_fragrance", input=SUBMITTED)]),
    ])
    details, alts, note = claude_provider.lookup("aventis")
    assert details.name == "Aventus" and details.source == "claude"
    assert details.top_notes == ["Pineapple", "Bergamot"]
    assert alts[0].label.endswith("Aventus Absolu")
    assert note.startswith("Read")
    assert len(messages.calls) == 2
    # the paused assistant turn is sent back as-is, with no extra user message
    assert [m["role"] for m in messages.calls[1]["messages"]] == ["user", "assistant"]
    assert messages.calls[0]["fallbacks"] == "default"


def test_nudges_when_claude_forgets_to_submit(monkeypatch):
    messages = fake_client(monkeypatch, [
        SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text="Here is what I found...")]),
        SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", name="submit_fragrance", input=SUBMITTED)]),
    ])
    assert claude_provider.lookup("aventus")[0].brand == "Creed"
    assert messages.calls[1]["messages"][-1]["role"] == "user"


def test_refusal_and_empty_results(monkeypatch):
    fake_client(monkeypatch, [SimpleNamespace(stop_reason="refusal", content=[])])
    with pytest.raises(claude_provider.ClaudeLookupError):
        claude_provider.lookup("x")
    empty = {**SUBMITTED, "accords": [], "top_notes": [], "middle_notes": [], "base_notes": [], "match_note": "Not a perfume"}
    fake_client(monkeypatch, [SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", name="submit_fragrance", input=empty)])])
    with pytest.raises(claude_provider.ClaudeLookupError, match="Not a perfume"):
        claude_provider.lookup("my cat")
