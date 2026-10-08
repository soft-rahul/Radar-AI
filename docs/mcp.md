# Use ShelfRadar from Claude, Cursor or any MCP client

ShelfRadar ships an MCP server, so an AI assistant can ask it how visible a brand is in AI search.
It reads the saved study, so it needs **no keys and spends no searches**. On a fresh clone it rebuilds
its database from the recorded SerpApi responses on first use.

## Tools

| Tool | What it returns | Cost |
|---|---|---|
| `list_brands` | Every tracked brand with its share of AI answers, 95% range and tier | 0 |
| `brand_visibility(brand)` | Overall, by engine and by language, named-first share, own-site citations, stance; aliases accepted | 0 |
| `citation_gap(brand, limit)` | Websites the AI cites when it names a rival but not this brand | 0 |
| `ask_live(question_id)` | One fresh answer per engine for a study question (q1–q5), with the brands named | ~4 SerpApi searches from your key; off unless `SHELFRADAR_ALLOW_LIVE=1` |

## Claude Code

This repository includes `.mcp.json`, so Claude Code offers the `shelfradar` server when you open the folder.
Or add it yourself:

```bash
claude mcp add shelfradar -- uv run --directory /path/to/shelfradar shelfradar-mcp
```

## Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit config), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "shelfradar": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/shelfradar", "shelfradar-mcp"]
    }
  }
}
```

To allow `ask_live`, add `"env": {"SHELFRADAR_ALLOW_LIVE": "1", "SERPAPI_KEY": "<your key>"}` to that entry.

## Try asking

- "Which whey protein brands do AI search engines recommend most in India?"
- "How visible is Nakpro, and where is it weakest?"
- "Which websites should MuscleBlaze get covered on to appear in more AI answers?"
