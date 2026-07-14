# Pokémon 30th Anniversary Preorder Bot

A local, one-shot script that checks a configurable list of retailer pages for
the Pokémon TCG **30th Celebration** products (releasing worldwide on
**September 16, 2026**), and fires a desktop notification the moment one
looks preorderable. It's meant to be run periodically via cron/Task
Scheduler — it does **not** run continuously and does **not** place orders
for you. It only detects and alerts.

## What's covered out of the box

Preloaded in [`config/targets.yaml`](config/targets.yaml):

- **Pokémon Center Germany** (`pokemoncenter.com/de-de`) and Pokémon Center
  US/international — official site opens preorders first, historically
  ~1 month before release (so watch for a mid-August 2026 window).
- **GameStop (US)** — already taking preorders on the Ultra Premium
  Collection at time of writing.
- **Amazon.de**, **Amazon.com**, **MediaMarkt.de**, **Otto.de**,
  **Target**, **Best Buy** — generic search-result pages.

Matched keywords include the confirmed product lineup: Elite Trainer Box,
Pokémon Center Elite Trainer Box, Ultra Premium Collection, Poster
Collection, Tech Sticker Collection, Eevee Knock Out Collection, Eevee
2-Pack Blister, Sylveon ex, Greninja ex, plus "30th Celebration" / "30th
Anniversary" / German equivalents.

Add more sites any time by appending an entry to `config/targets.yaml` — no
code changes needed (see comments in that file for the two supported target
shapes).

**Important limitation:** Amazon, GameStop, Walmart, Target and Best Buy run
bot-detection (Akamai/PerimeterX/Cloudflare). Even rendering with a real
browser, these can occasionally return a CAPTCHA/blocked page instead of the
real one — treat hits/misses on those as best-effort, and don't shorten the
polling interval to compensate (see "Etiquette" below). The official
Pokémon Center site and MediaMarkt/Otto are far more reliable targets.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium      # downloads the headless browser used for JS-heavy sites
```

## Running it

```bash
source .venv/bin/activate
python -m pokemon_preorder_bot.main
```

This checks every target in `config/targets.yaml` once, prints a summary
table, writes full details to `data/latest_results.json`, appends to
`data/bot.log`, and fires a desktop notification (via `plyer`) for any
product whose status just flipped to "available" (state is remembered in
`data/state.json`, so you're only notified on *changes*, not every run).

Verify notifications work on your machine before relying on them:

```bash
python -m pokemon_preorder_bot.main --test-notify
```

- Linux: needs `notify-send` (usually in the `libnotify-bin`/`libnotify`
  package) installed.
- macOS/Windows: works out of the box via `plyer`.

If a notification backend isn't available, alerts still always get written
to `data/bot.log` and `data/latest_results.json` as a fallback — check
those if you don't trust notifications alone.

## Scheduling it

**cron (Linux/macOS)** — every 20 minutes, log any crashes to a separate file:

```cron
*/20 * * * * cd /path/to/window-pinner && .venv/bin/python -m pokemon_preorder_bot.main >> data/cron.out 2>&1
```

**Windows Task Scheduler** — create a basic task that runs
`.venv\Scripts\python.exe -m pokemon_preorder_bot.main` with "Start in"
set to the project folder, triggered every 20-30 minutes.

## Etiquette / why it's this way

- Requests are spaced out (`--delay`, default 3s between sites) and retried
  with backoff rather than hammered — keep the cron interval at 15-30
  minutes. Checking more often doesn't get you a faster answer (Pokémon
  Center itself is the fastest-moving target and only updates its own
  catalog a few times a day) and just increases the odds a retailer starts
  blocking your IP.
- This only reads public pages — no login, no checkout automation, no
  purchasing. Some retailers' Terms of Service restrict automated access;
  this tool is intended for light, personal, informational use (checking a
  handful of pages a few times an hour), not high-frequency or commercial
  scraping.

## How matching works (and its limits)

For search/listing pages, the bot parses the DOM and, for each keyword hit,
climbs to the enclosing element that looks like a product card (by class
name, e.g. `product`, `card`, `tile`) so that "in stock" text from a
*different* product on the same page doesn't get attributed to your match.
It then looks for known available/unavailable phrases (English + German) in
that card's text. This is a heuristic — it can occasionally miscall an
unusual page layout as "unknown" or misjudge a genuinely ambiguous listing.
`data/latest_results.json` includes the matched text snippet for every
target so you can sanity check a result yourself before acting on it.

## Project layout

```
config/targets.yaml           # sites to watch — edit this to add/remove targets
pokemon_preorder_bot/
  config.py                   # loads targets.yaml
  fetcher.py                  # static (requests) + JS-rendered (playwright) page fetching
  matcher.py                  # keyword + availability-status detection
  state.py                    # remembers last status per (target, keyword) to dedupe alerts
  notifier.py                 # desktop notification + logging
  main.py                     # CLI entry point
data/                         # state.json, latest_results.json, bot.log (gitignored)
tests/                        # matcher unit tests
```

## Extending

- **Add a retailer:** copy an existing block in `config/targets.yaml`,
  point `url` at the product or search page, set `render: true` if the site
  needs JavaScript to show content (most modern storefronts do).
- **Add more product keywords:** edit the shared `keywords` list at the top
  of `config/targets.yaml` (all targets reference it via a YAML anchor).
- **Change notification channel:** swap the body of `notify()` in
  `pokemon_preorder_bot/notifier.py` for an email/Discord/Telegram call if
  you'd rather not rely on desktop notifications.
