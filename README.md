# Pokémon 30th Anniversary Preorder Bot

A local, one-shot script that checks a configurable list of retailer pages for
the Pokémon TCG **30th Celebration** products (releasing worldwide on
**September 16, 2026**, branded "30th CELEBRATION" in every region including
Japan), and fires a desktop notification the moment one looks orderable. It's
meant to be run periodically via cron/Task Scheduler — it does **not** run
continuously and does **not** place orders for you. It only detects and
alerts.

## What's covered out of the box

Preloaded in [`config/targets.yaml`](config/targets.yaml), focused on
Germany, international, and Japan (no US local-store chains):

- **Germany:** Pokémon Center Germany (`pokemoncenter.com/de-de`),
  Amazon.de, MediaMarkt.de, Otto.de.
- **International:** Pokémon Center (`en-us`), Amazon.com.
- **Japan:** Pokémon Center Japan's 30th-anniversary feature page
  (`pokemoncenter-online.com`), Amazon.co.jp. Japan's release is partly
  lottery/invitation-based (抽選 / 招待リクエスト) rather than a plain
  preorder button — the bot recognizes that wording too.

Add more sites any time by appending an entry to `config/targets.yaml` — no
code changes needed (see comments in that file), or use the web dashboard's
**Manage Targets** page.

**Important limitation:** Amazon and other large storefronts run
bot-detection (Akamai/PerimeterX/Cloudflare). Even rendering with a real
browser, these can occasionally return a CAPTCHA/blocked page instead of the
real one — treat hits/misses on those as best-effort, and don't shorten the
polling interval to compensate (see "Etiquette" below). The official
Pokémon Center sites and MediaMarkt/Otto are generally more reliable.

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
`data/bot.log`, and fires a desktop notification (via `plyer`) the moment a
product's status flips to **preorder** or **in_stock** (state is remembered
in `data/state.json`, so you're only notified on *changes*, not every run).

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

## Web dashboard

A local Flask app gives you a visual view and lets you manage everything
without touching YAML or the CLI by hand:

```bash
source .venv/bin/activate
python -m webapp.app
```

Open **http://127.0.0.1:5000** (binds to localhost only) in a browser **on the
same machine you ran that command on**. If port 5000 is already taken by
something else on your computer (common on macOS, where AirPlay Receiver
listens on 5000 - Firefox/Chrome will show a `403 Forbidden` right from the
process squatting the port, not from this app), run it on a different port
instead:

```bash
PORT=5050 python -m webapp.app   # then open http://127.0.0.1:5050
```

From there you can:

- **Dashboard** — see every target's last-known status as a color-coded card
  (preorder / in_stock / unavailable / unknown / error / disabled), with the
  matched text snippet and a link to the site. "Run check now" triggers a
  full check on demand (takes ~1-2 minutes for all rendered sites); "Send
  test notification" verifies desktop notifications work on your machine.
- **Manage Targets** — add a new site, edit an existing one's URL/keywords/
  status phrases, enable/disable a target without deleting it, or delete it
  entirely. Changes are saved straight to `config/targets.yaml`.
- **Logs** — tail the last ~300 lines of `data/bot.log`.

The dashboard and the `python -m pokemon_preorder_bot.main` CLI read/write
the same `config/targets.yaml` and `data/` files, so a cron job and the web
UI stay in sync automatically — use the UI to manage targets and glance at
results, and cron/Task Scheduler to keep checks running in the background.

Note: saving any change via **Manage Targets** rewrites `config/targets.yaml`
in full, which drops the descriptive header comments at the top of the
shipped file (the settings themselves, including the shared keyword lists,
are preserved).

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

Matching happens in two tiers, specifically to avoid false positives like a
generic "30th anniversary" search on Amazon pulling in the unrelated
"Pokémon Day 2026 Collection" or 30th-anniversary plush/apparel that share
the word "30th" but aren't this TCG release:

1. **Set keywords** (`keywords` in the config) identify the release itself —
   e.g. `"30th Celebration"`.
2. **Product keywords** (`product_keywords`), if given, require a card to
   *also* contain a real product-type term (Elite Trainer Box, Ultra Premium
   Collection, Sylveon ex, etc. — or their Japanese equivalents like 拡張パック
   / デッキ / BOX for the Japan targets) before it counts as a hit. A card
   that only shares the set keyword — like an anniversary plush — is
   filtered out.

For search/listing pages, the bot parses the DOM and, for each keyword hit,
climbs to the enclosing element that looks like a product card (by class
name, e.g. `product`, `card`, `tile`) so that status text from a *different*
product on the same page doesn't get attributed to your match. It then
classifies that card's text into one of four statuses, in order of
precedence:

- **`preorder`** — explicit preorder/reservation/lottery-entry wording found
  (English/German/Japanese, including Japan's 抽選予約 / 招待リクエスト
  invitation-lottery system). This is the strongest, most specific signal
  and wins even if a generic "sold out" phrase also appears elsewhere in a
  noisy card.
- **`in_stock`** — a plain buy-now/add-to-cart signal, no preorder wording.
  Notable mainly if a seller lists it as already released.
- **`unavailable`** — sold out / not yet listed / notify-me wording, no
  preorder or buy signal.
- **`unknown`** — the product is mentioned but no recognizable status text
  is nearby (or the card contains contradictory signals, e.g. a disabled
  "Add to Cart" button next to "Out of Stock" text for a different variant —
  deliberately not guessed either way).

Notifications fire on `preorder` and `in_stock` (the "you can act now"
states). This is still a heuristic — it can occasionally miscall an unusual
page layout as `unknown`, or a genuinely ambiguous listing. Both
`data/latest_results.json` and the dashboard show the matched text snippet
for every hit so you can sanity check a result yourself before acting on it.

## Project layout

```
config/targets.yaml           # sites to watch — edit this to add/remove targets
pokemon_preorder_bot/
  config.py                   # loads/saves targets.yaml
  fetcher.py                  # static (requests) + JS-rendered (playwright) page fetching
  matcher.py                  # two-tier keyword matching + status classification
  state.py                    # remembers last status per (target, keyword) to dedupe alerts
  notifier.py                 # desktop notification + logging
  main.py                     # CLI entry point
webapp/                       # local Flask dashboard (see "Web dashboard" above)
data/                         # state.json, latest_results.json, bot.log (gitignored)
tests/                        # matcher unit tests
```

## Extending

- **Add a retailer:** copy an existing block in `config/targets.yaml` (or use
  **Manage Targets** in the dashboard), point `url` at the product or search
  page, set `render: true` if the site needs JavaScript to show content
  (most modern storefronts do).
- **Tighten or loosen matching:** adjust `product_keywords` per target — add
  more product-type terms to catch more listings, or leave it empty to match
  on the set keyword alone (useful for a page that's already 100% dedicated
  to this release, like the Pokémon Center Japan feature page).
- **Change notification channel:** swap the body of `notify()` in
  `pokemon_preorder_bot/notifier.py` for an email/Discord/Telegram call if
  you'd rather not rely on desktop notifications.
