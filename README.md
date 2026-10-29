# Sillage

A personal fragrance-collection tracker that learns your taste.

- **Add by name.** Type any fragrance ("tom ford tobacco vanille", "BR540", or a Fragrantica link) and Sillage fetches it from the web: brand, year, perfumer, olfactive family, main accords, and the top / heart / base notes. Nothing is hard-coded.
- **Say how you feel.** Rate each one Love / Like / Neutral / Dislike / Hate, and track it as owned, sample, decant, wishlist, tried or sold.
- **Log what you wear.** Record the date, occasion, weather, sprays, how much you enjoyed it, and compliments.
- **See your patterns.** The insights page shows:
  - the notes and accords you love and the ones you avoid
  - polarizing notes
  - your best families, perfumers and brands
  - what you wear each season and for each occasion
  - your most-worn and most-complimented scents, and bottles you've neglected
- **Predict before you buy.** Every lookup and wishlist item gets a match score ("85% — very likely your style"), along with the notes driving it.
- **AI scent profile (optional).** Claude explains your taste in plain words, recommends what to try next and what to skip, and points out gaps in your collection.

## Run it

```bash
./run.sh
```

Then open http://127.0.0.1:8000. The first run creates a virtualenv and installs the dependencies. Your data lives in `data/sillage.db`, a single SQLite file, so back it up by copying it. `GET /api/export` also dumps everything as JSON.

To turn on the AI features (strongly recommended):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
./run.sh
```

To explore with sample data first:

```bash
python -m scripts.demo_data data/demo.db
SILLAGE_DB=data/demo.db ./run.sh
```

## How lookups work

The lookup tries two sources in order:

1. **Fragrantica, directly.** It finds the perfume's page through DuckDuckGo (or Bing as a fallback) with `site:fragrantica.com/perfume <your text>`. When several results come back, it ranks them so the original beats its flankers unless you named the flanker. It then parses the page. Parsing starts from the meta description, whose wording Fragrantica rarely changes. It then reads the HTML notes pyramid and the accord bars, which give the accord strengths. Pages are fetched with `curl_cffi`, which mimics a real Chrome browser, so Fragrantica's Cloudflare protection blocks it less often.
2. **Claude web research.** This needs `ANTHROPIC_API_KEY`. Claude searches the web, reads the Fragrantica page (or Parfumo, Basenotes or the brand's site if Fragrantica isn't reachable), and returns the data in a strict schema. It handles typos, nicknames and ambiguous names, and explains how it read your query, e.g. *"Read 'savage' as Dior Sauvage EDT (2015); did you mean the Elixir?"*. Claude is also used when the direct search returns a poor match.

Before you save, you see a preview showing the source link, "Not the one?" alternatives, and a duplicate warning. You can correct any field, or enter a fragrance by hand. **Refresh from web** re-fetches the notes later and keeps your ratings. **Add many at once** takes a list, one fragrance per line, which is handy when you're entering a big collection.

You can change the source order with `SILLAGE_LOOKUP_ORDER`, e.g. `claude,fragrantica` or `fragrantica`.

> Fragrantica has no public API and actively blocks scrapers. If direct lookups start failing, either it changed its layout or your IP got rate-limited. With an API key set, the Claude path keeps working, and pasting a Fragrantica URL still works when search is blocked.

## How the taste model works

`app/insights.py`, plain statistics, no AI:

- **Fragrance score.** Your rating (−2 … +2), blended 70/30 with the average enjoyment from your wears. A wear-frequency bonus of up to +1 is added for scents you actually reach for, because what you wear says as much as what you say you like. Wishlist items are never learned from.
- **Note and accord affinity.** A weighted average of the scores of the fragrances that contain the note:
  - It is shrunk toward zero, so a note seen once ranks below one that appears across five favourites.
  - Heart and base notes weigh more than top notes.
  - Accords are weighted by Fragrantica's bar strength.
  - Regional variants are grouped together ("Calabrian bergamot" counts as bergamot, "Madagascar vanilla" as vanilla).
- **Match score.** The affinities of a new fragrance's notes and accords, weighted by how much evidence backs each one. With little overlap the score is pulled toward 50%.

## Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables smart lookups and the AI profile |
| `SILLAGE_DB` | `data/sillage.db` | Database file |
| `SILLAGE_LOOKUP_ORDER` | `fragrantica,claude` | Lookup provider order |
| `SILLAGE_MODEL` | `claude-opus-5-5` | Claude model |
| `SILLAGE_AI` | `on` | `off` disables all Claude calls |
| `SILLAGE_HEMISPHERE` | `north` | `south` flips the seasons |

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

Layout: `app/main.py` (API), `app/db.py` (SQLite), `app/lookup/` (Fragrantica + Claude providers), `app/insights.py` (taste model), `app/ai_profile.py`, `app/static/` (frontend: vanilla JS, no build step).
