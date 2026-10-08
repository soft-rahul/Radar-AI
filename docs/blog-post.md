# We asked Google and Bing's AI 90 times which whey protein Indians should buy

_Draft for the SerpApi blog / BuiltWithSerpApi. Every number comes from [report.md](report.md)._

"Which is the no. 1 whey protein in India?" is now answered by an AI before anyone clicks a link. So which brands do Google AI Overview, Google AI Mode and Bing Copilot actually put in front of Indian shoppers, and can you trust a single answer to tell you?

We built ShelfRadar with SerpApi to find out. It asks real buying questions, taken from Google autocomplete and People Also Ask, in English, Hindi and Hinglish (Hindi typed in Roman letters, the way many Indians search). Each question goes to three AI engines, more than once, and every share comes with a 95% range.

## Ask twice, get a different answer

The most useful result is also the simplest. When we asked the same question to the same engine in the same language a second time, the list of brands changed in **33 of 40 cases**. A screenshot of one AI answer is a roll of the dice. Visibility has to be measured as a probability, which means sampling, which means a search API you can call repeatedly with `no_cache=true`.

## Google and Bing see different markets

Indian direct-to-consumer brands do far better on Google. AS-IT-IS appears in 93% of Google AI Mode answers but 47% of Bing Copilot answers; Nakpro in 63% vs 23%. Copilot leans towards imported brands instead: Isopure is named in half of its answers.

## Hindi answers built from English pages

Google's AI Overview answered Hindi questions mostly from English websites served through Google Translate: 25 of its 38 sources. Copilot cited no translated pages; 97 of its 100 sources for Hindi questions had Hindi titles. For a brand, that means Hindi visibility on Google depends on English content, and on Bing on Hindi content.

## Where the AI gets its picks

88% of AI Overview sources were not on Google's first page for the same search. Review blogs and shops were cited far more often than brands' own websites. ShelfRadar turns this into an outreach list: the sites the AI cites when it recommends a rival but not you.

## How SerpApi made this possible

- `google` gives the AI Overview and the organic top 10 from the same page, so the two can be compared directly.
- `google_ai_overview` with `page_token` catches overviews that Google defers, which happened most for Hindi.
- `google_ai_mode` and `bing_copilot` give two more AI engines in the same JSON shape.
- `location`, `gl` and `hl` put the questions in Mumbai and Delhi, in English and Hindi.
- The Locations API caught that a bare "Delhi" also matches Delhi, Ohio.

The whole study took 100 searches. Every response is saved, so the dashboard, the report and the MCP server replay it offline with no keys.

## Try it

The code, the data and a one-command demo are on GitHub: `make demo`. Ask Claude about any brand through the MCP server, or point it at your own category by editing two JSON files.
