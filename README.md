# Pokémon 30th Anniversary Preorder Bot

A local script that checks a configurable list of retailer pages for the
Pokémon TCG **30th Celebration** products (releasing worldwide on
**September 16, 2026**, branded "30th CELEBRATION" in every region including
Japan), and fires a desktop notification the moment one looks orderable. Two
ways to keep it running: a one-shot CLI you schedule with cron/Task
Scheduler, or the web dashboard's built-in auto-check, which keeps checking
on a timer for as long as you leave that dashboard process running. Either
way, it only detects and alerts — it never places an order for you.

## What's covered out of the box

Preloaded in [`config/targets.yaml`](config/targets.yaml), focused on
Germany, international, and Japan (no US local-store chains):

- **Germany (general retail):** Pokémon Center Germany
  (`pokemoncenter.com/de-de`), Amazon.de, MediaMarkt.de, Otto.de.
- **Germany (TCG specialty shops):** JK-Entertainment, Games-Island
  (covers both "Game-Island" and "Gamesisland" — same shop, `games-island.eu`),
  Card-Corner, Trader-Online (TRADER), Gate to the Games, cardcosmos
  (whose `tcg-vorbestellung` collection page is *already* preorder-scoped —
  probably the single most useful target in the whole list), TCGViert,
  TOPTCG.de, TCG-Trade.
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
Pokémon Center sites, MediaMarkt/Otto, and the smaller TCG specialty shops
are generally more reliable — smaller shops in particular tend to run much
lighter bot-detection than Amazon/big-box retail.

**On verifying this actually works against every listed site:** the URLs
above are real (each was confirmed to exist via web research while adding
it), and the matching logic is covered by unit tests against realistic page
fixtures — but I cannot personally load these pages from where I run, since
outbound network access here is sandboxed and blocks exactly this kind of
request. So: correct URLs and correct matching logic, verified separately,
but never combined into one live end-to-end check against the real sites.
The first `python -m pokemon_preorder_bot.main` run on your own machine
*is* that missing check. If a target comes back `error` repeatedly (see
`data/bot.log` for the reason) or `no_match` when you can see the product
listed with your own eyes, that's useful signal — tell me which target and
what the page actually shows, and the URL or selectors can be adjusted.

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

**Optional: Discord.** Set a `DISCORD_WEBHOOK_URL` environment variable
(Discord → server settings → Integrations → Webhooks → New Webhook → Copy
URL) and every alert also posts there — handy since it reaches your phone
through the Discord app without relying on desktop notifications at all:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m pokemon_preorder_bot.main
```

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
  (preorder / in_stock / unavailable / blocked / unknown / error / disabled),
  with the matched text snippet and a link to the site. "Run check now"
  triggers a full check on demand; "Send test notification" verifies
  desktop/Discord notifications work. The page quietly refreshes itself once
  a minute (paused while the tab is hidden, and never while you're typing in
  the interval field) so it reflects auto-check activity without you having
  to hit reload.
- **Auto-check** — starts automatically the moment you launch the dashboard
  (`python -m webapp.app`) and re-checks every 20 minutes (configurable, 5 min
  minimum) for as long as that process keeps running - this is the "solange
  der Bot läuft" mode. Pause/resume it with one click. It shares a lock with
  the manual "Run check now" button, so the two can never run concurrently
  and corrupt `state.json`/`latest_results.json` — if one is already in
  flight, the other just skips that click instead of queuing up.
- **Manage Targets** — add a new site, edit an existing one's URL/keywords/
  status phrases, enable/disable a target without deleting it, or delete it
  entirely. Changes are saved straight to `config/targets.yaml`.
- **Logs** — tail the last ~300 lines of `data/bot.log`.

The dashboard and the `python -m pokemon_preorder_bot.main` CLI read/write
the same `config/targets.yaml` and `data/` files. Pick one primary way to
keep checks running - either cron/Task Scheduler with the dashboard closed
most of the time, or the dashboard's own auto-check left open - rather than
both at once, which would just double the request rate against every site.

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

- Requests are spaced out (`--delay`, default 3s between sites, jittered up
  to 2x so the interval isn't perfectly uniform) and retried with backoff
  rather than hammered — keep the check interval (cron or auto-check) at
  15-30 minutes either way. Checking more often doesn't get you a faster
  answer (Pokémon Center itself is the fastest-moving target and only
  updates its own catalog a few times a day) and just increases the odds a
  retailer starts blocking your IP.
- This only reads public pages — no login, no checkout automation, no
  purchasing. Some retailers' Terms of Service restrict automated access;
  this tool is intended for light, personal, informational use (checking a
  handful of pages a few times an hour), not high-frequency or commercial
  scraping.

## How matching works (and its limits)

**Structured data first.** Before guessing from visible button text, the bot
looks for schema.org `Product`/`Offer` JSON-LD blocks in the page - most
modern storefronts embed these for search engines regardless of what JS
framework renders the visible page, and their `availability` field
(`InStock`, `PreOrder`, `OutOfStock`, etc.) is a far more reliable signal
than text heuristics. If a matching product is found there, that's used
directly; the DOM/text heuristic below only runs as a fallback when no
usable structured data is present.

Matching also happens in two tiers, specifically to avoid false positives
like a generic "30th anniversary" search on Amazon pulling in the unrelated
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
name, e.g. `product`, `card`, `tile`, `sku`) so that status text from a
*different* product on the same page doesn't get attributed to your match.
That climb deliberately stops - and the match is dropped entirely rather
than trusted - if the resulting container's text is too large to plausibly
be a single product card (over ~600 characters): on pages with no
distinguishing per-item classes, climbing without that cap could reach all
the way up to the wrapper around the *entire results grid*, at which point
any product-keyword found anywhere else on the page (a totally unrelated
listing) would incorrectly count as confirming the match. Better to miss an
occasional real hit on an unusually-marked-up page than to report one
stitched together from two unrelated products - if that happens on a
specific site, it'll show up as `no_match` instead of a wrong `preorder`.

Each distinct product gets its own tracked identity (set keyword + the
specific product term that matched, e.g.
`"30th Celebration — Elite Trainer Box"`) so that two different products
found on the same search page are remembered separately instead of
overwriting each other's status between runs. It then classifies each
card's text into one of four statuses, in order of precedence:

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

A separate `blocked` status means the page looked like a bot-detection/
CAPTCHA challenge rather than real content - worth knowing about since it's
different from a legitimate "nothing listed yet" (`no_match`). Its detail
text always shows exactly which phrase matched and the surrounding snippet
(e.g. `matched "checking your browser": …Checking your browser before
accessing…`) rather than a generic "you got blocked" message - this is
deliberate: an earlier version's check for bare `"captcha"` also matched
`"reCAPTCHA"`, which shows up in the routine legal-disclosure footer text
nearly every ordinary shop embeds for its contact form, so *every* target
came back `blocked` regardless of whether it actually was. Surfacing the
matched text is what made that diagnosable in the first place - if `blocked`
ever looks wrong again, the detail line says why; the phrase list in
`matcher.py`'s `_BLOCKED_PHRASES` can be trimmed or extended from there.

A `product_page` target (a single specific product URL, once real ones
exist for this release) also gets flagged `no_match` if its keywords no
longer appear on the page at all, since that usually means the URL went
stale (delisted, redirected) rather than that the product is simply
unavailable.

**Fetching reliability notes:**
- One Chromium instance is now shared across all JS-rendered targets in a
  single run instead of launching a fresh browser per site - faster, and
  matters more now that a run can happen automatically every few minutes.
- The browser's locale/language header is picked based on the target's
  domain (`.de`/`de-de` → German, `.co.jp`/`pokemoncenter-online.com` →
  Japanese, else English), so a German or Japanese storefront isn't
  accidentally rendered in English/USD.
- A best-effort attempt is made to click through common cookie-consent
  banners (English/German/Japanese button text) before reading the page,
  since a banner sitting on top of the product grid would otherwise hide
  the real content from the text heuristic.

## Project layout

```
config/targets.yaml           # sites to watch — edit this to add/remove targets
pokemon_preorder_bot/
  config.py                   # loads/saves targets.yaml
  fetcher.py                  # static (requests) + JS-rendered (playwright) page fetching
  matcher.py                  # two-tier keyword matching + status classification
  state.py                    # remembers last status per (target, keyword) to dedupe alerts
  notifier.py                 # desktop + Discord notification + logging
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
  to this release, like the Pokémon Center Japan feature page or cardcosmos'
  preorder collection). The shared `product_keywords_intl` list already
  includes both the current English product names and older/German shop
  terms (`Top-Trainer-Box`, `Booster Display`, `36er Display`,
  `Sammelkoffer`) since the TCG specialty shops don't always use the same
  wording as Amazon/MediaMarkt.
- **Change notification channel:** Discord is already built in (see
  `DISCORD_WEBHOOK_URL` above) — for email/Telegram/something else, add a
  call next to `_notify_discord()` in `pokemon_preorder_bot/notifier.py`.
