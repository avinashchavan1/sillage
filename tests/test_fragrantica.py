from pathlib import Path

import pytest

from app.lookup import fragrantica
from app.text import infer_concentration, match_score, note_key, split_list

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_perfume_page():
    html = (FIXTURES / "fragrantica_sauvage.html").read_text()
    d = fragrantica.parse_perfume_page(html, "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html")
    assert d.name == "Sauvage"
    assert d.brand == "Dior"
    assert d.year == 2015
    assert d.gender == "Men"
    assert d.family == "Aromatic Fougere"
    assert d.perfumers == ["François Demachy"]
    assert d.top_notes == ["Calabrian bergamot", "Pepper"]
    assert d.middle_notes == ["Sichuan Pepper", "Lavender", "Pink Pepper", "Vetiver", "Patchouli", "Geranium", "Elemi"]
    assert d.base_notes == ["Ambroxan", "Cedar", "Labdanum"]
    assert "Oud" not in d.all_notes()  # link outside the pyramid
    assert [a.name for a in d.accords][:3] == ["fresh spicy", "amber", "citrus"]
    assert d.accords[0].strength == 1.0 and d.accords[1].strength == pytest.approx(0.725)
    assert d.community_rating == pytest.approx(4.05)
    assert d.rating_votes == 31204
    assert d.image_url.endswith("31861.jpg")
    assert d.source == "fragrantica"


def test_description_only_fallback():
    desc = (
        "Libre by Yves Saint Laurent is a Amber Fougere fragrance for women. Libre was launched in 2019. "
        "The noses behind this fragrance are Anne Flipo and Carlos Benaim. Top notes are Lavender, Mandarin "
        "Orange and Black Currant; middle notes are Lavender, Orange Blossom and Jasmine; base notes are "
        "Madagascar Vanilla, Musk, Cedar and Ambergris."
    )
    html = f'<html><head><meta name="description" content="{desc}"></head><body></body></html>'
    d = fragrantica.parse_perfume_page(html)
    assert (d.name, d.brand, d.gender, d.year) == ("Libre", "Yves Saint Laurent", "Women", 2019)
    assert d.perfumers == ["Anne Flipo", "Carlos Benaim"]
    assert d.top_notes == ["Lavender", "Mandarin Orange", "Black Currant"]
    assert d.base_notes[-1] == "Ambergris"


def test_flat_pyramid_from_description():
    desc = "Molecule 01 by Escentric Molecules is a Woody fragrance for women and men. The main notes are Iso E Super."
    html = f'<html><head><meta name="description" content="{desc}"></head><body></body></html>'
    d = fragrantica.parse_perfume_page(html)
    assert d.gender == "Unisex"
    assert d.notes == ["Iso E Super"]


def test_page_without_notes_raises():
    with pytest.raises(fragrantica.FragranticaError):
        fragrantica.parse_perfume_page("<html><head><title>Just a page</title></head><body></body></html>")


def test_extract_and_rank_search_results(monkeypatch):
    html = (FIXTURES / "ddg_results.html").read_text()
    urls = fragrantica.extract_perfume_urls(html)
    assert len(urls) == 3  # news link ignored
    monkeypatch.setattr(fragrantica, "_get", lambda *a, **k: html)
    ranked = fragrantica.search("dior sauvage")
    assert ranked[0] == "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"  # original beats flankers
    assert fragrantica.search("sauvage elixir")[0].endswith("Sauvage-Elixir-68415.html")


def test_lookup_uses_search_then_page(monkeypatch):
    ddg = (FIXTURES / "ddg_results.html").read_text()
    page = (FIXTURES / "fragrantica_sauvage.html").read_text()
    monkeypatch.setattr(fragrantica, "_get", lambda url, **k: page if "fragrantica" in url else ddg)
    details, alts = fragrantica.lookup("Dior Sauvage")
    assert details.name == "Sauvage"
    assert len(alts) == 2 and all("Sauvage" in a.label for a in alts)


def test_canonical_url():
    assert (
        fragrantica.canonical_url("https://fragrantica.fr/perfume/Dior/Sauvage-31861.html?x=1")
        == "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"
    )
    assert fragrantica.canonical_url("https://example.com") is None


def test_text_helpers():
    assert split_list("Bergamot, Pepper and Lemon.") == ["Bergamot", "Pepper", "Lemon"]
    assert note_key("Calabrian bergamot") == "bergamot"
    assert note_key("Madagascar Vanilla Absolute") == "vanilla"
    assert note_key("Pink Pepper") == "pink pepper"
    assert infer_concentration("Sauvage Eau de Parfum") == "Eau de Parfum"
    assert infer_concentration("Sauvage") is None
    assert match_score("dior sauvage", "Dior Sauvage") == 1.0
    assert match_score("dior sauvage", "Dior Sauvage Elixir") < 1.0
    assert match_score("tobaco vanile tom ford", "Tom Ford Tobacco Vanille") > 0.9
    assert match_score("aventus", "Dior Sauvage") < 0.5
