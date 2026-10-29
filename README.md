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

## AI platform

Sillage stores your collection and exposes it to any AI. Other AI apps can query it, analyse it and publish their
findings back into the app.

```
            ┌──────────── Sillage server ───────────────┐
 Claude ───►│ /mcp   MCP server (Streamable HTTP)       │
 ChatGPT ──►│ /api   REST + OpenAPI (/docs)             │──► SQLite: collection, wears, insights
 Cursor ───►│ stdio  python -m app.mcp_server (local)   │
 scripts ──►│ one shared tool registry (app/tools.py)   │◄── web app: Ask Sillage chat + Insights feed
            └───────────────────────────────────────────┘
```

- **14 tools**, each one written once (in `app/tools.py`) and served over MCP, over REST (`POST /api/tools/{name}`) and to the
  in-app assistant:
  - **Read:** `collection_overview`, `search_fragrances`, `get_fragrance`, `taste_profile`, `wear_history`,
    `query_database` (read-only SQL with documented views), `evaluate_fragrance` ("would I like X?" for fragrances you
    don't own), `similar_fragrances`, `suggest_what_to_wear` (weather- and occasion-aware), `list_insights`.
  - **Write:** `add_fragrance`, `update_fragrance`, `log_wear`, and `publish_insight`, which pushes a finding with a
    bar, diverging, line or table chart into the app's **AI insights** feed.
- **Ask Sillage** (the **✦ Ask AI** tab) is a streaming Claude agent using the same tools. It shows each step it takes, and any
  answer can be saved to your insights.
- **MCP extras:** read-only/write hints on every tool, `sillage://schema` and `sillage://taste-profile` resources,
  and `analyze-my-taste` and `what-to-wear` prompts.
- **Security:**
  - Every `/api` and `/mcp` call needs your key (`Authorization: Bearer …`, `X-API-Key`, or `?key=` for connector UIs that
    can't send headers).
  - SQL runs on a read-only connection, one statement at a time, with a 3-second / 500-row budget.
  - On your own computer the web app picks up the key automatically. Once deployed, it asks you for it.

The **Connect** tab shows copy-paste setup for each client, with your real key filled in:

```bash
# Claude Code
claude mcp add --transport http sillage http://127.0.0.1:8000/mcp --header "Authorization: Bearer <key>"
```

Claude Desktop and Cursor run the local stdio server (`python -m app.mcp_server`); the Connect tab gives you the exact
JSON to paste. claude.ai and ChatGPT connectors and Custom GPT Actions need a public HTTPS URL (see Deploy).

## Deploy (for web AI apps)

```bash
fly launch --no-deploy --copy-config
fly volumes create sillage_data --size 1
fly secrets set ANTHROPIC_API_KEY=sk-ant-... SILLAGE_PUBLIC_URL=https://<your-app>.fly.dev SILLAGE_ALLOWED_HOSTS=<your-app>.fly.dev
fly deploy
fly logs   # the API key is printed at startup
```

The `Dockerfile` also works on any container host (Render, Railway, a VPS). Mount a volume at `/data`, and set
`SILLAGE_PUBLIC_URL` and `SILLAGE_ALLOWED_HOSTS`.

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

1. **Fragrantica, directly.** It finds the perfume's page through DuckDuckGo (or Bing as a fallback) with `site:fragrantica.com/perfume <your text>`. When several results come back, it ranks them so the original beats its flankers unless you named the flanker. It then parses the page. Parsing starts from the meta description, whose wording Fragrantica rarely changes. It then reads the HTML notes pyramid and the accord bars, which give the accord strengths. Requests identify themselves honestly as Sillage. If Fragrantica's bot protection refuses one, Sillage accepts that and falls back to step 2 rather than trying to get around it.
2. **Claude web research.** This needs `ANTHROPIC_API_KEY`. Claude searches the web, reads the Fragrantica page (or Parfumo, Basenotes or the brand's site if Fragrantica isn't reachable), and returns the data in a strict schema. It handles typos, nicknames and ambiguous names, and explains how it read your query, e.g. *"Read 'savage' as Dior Sauvage EDT (2015); did you mean the Elixir?"*. Claude is also used when the direct search returns a poor match.

Before you save, you see a preview showing the source link, "Not the one?" alternatives, and a duplicate warning. You can correct any field, or enter a fragrance by hand. **Refresh from web** re-fetches the notes later and keeps your ratings. **Add many at once** takes a list, one fragrance per line, which is handy when you're entering a big collection.

You can change the source order with `SILLAGE_LOOKUP_ORDER`, e.g. `claude,fragrantica` or `fragrantica`.

> Fragrantica has no public API and may refuse automated requests. When it does, lookups fall back to Claude web research (with an API key set), and you can always enter a fragrance by hand.

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
| `SILLAGE_API_KEY` | generated into `data/api_key` | Key for `/api` and `/mcp` |
| `SILLAGE_PUBLIC_URL` | — | Public HTTPS base URL once deployed (turns off key auto-pickup) |
| `SILLAGE_ALLOWED_HOSTS` | — | Extra Host names the MCP endpoint accepts, comma-separated |

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

Layout: `app/main.py` (API + MCP mount), `app/tools.py` (the tool registry), `app/mcp_server.py`, `app/assistant.py` (Ask Sillage), `app/auth.py`, `app/db.py` (SQLite + read-only SQL), `app/lookup/` (Fragrantica + Claude providers), `app/insights.py` (taste model), `app/recommend.py` (similarity + wear suggestions), `app/static/` (frontend: vanilla JS, no build step).
