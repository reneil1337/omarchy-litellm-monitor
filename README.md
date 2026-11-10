# LiteLLM Monitor — Omarchy shell plugin

Charts your LiteLLM proxy usage in the **AI Usage** bar panel and feeds the
stock omarchy agents panel: tokens by day for up to a year, a per-model
breakdown, and request counts.

A headless **service** polls your LiteLLM proxy every 10 minutes and records usage to
`~/.local/state/omarchy/agents/usage/litellm.json`. Two panels read it:

- **AI Usage** (this plugin's bar widget, see [below](#ai-usage-panel)) draws
  the charts: a LiteLLM tab with totals and trends, tokens by day, calendar week, or month across week, month,
  quarter, and year views, and tokens by model.
- **`omarchy.agents`** shows the 7-day heading and counts LiteLLM into its
  rotating summary line. Since Omarchy 4.5 it draws no charts for any agent,
  and plugins cannot add any to it.

## Install

```sh
omarchy plugin add https://github.com/reneil1337/omarchy-litellm-monitor --enable
```

For the charts, add the AI Usage widget next to the agents icon (skip this if
the Venice Monitor's AI Usage widget is already on the bar; it shows the
LiteLLM tab too) and restart the shell once:

```sh
omarchy bar put io.github.reneil1337.litellm --after omarchy.agents
omarchy restart shell
```

## AI Usage panel

Since Omarchy 4.5 the stock agents panel draws no charts for any agent, and
plugins cannot add any to it. This plugin therefore ships its own bar widget,
**AI Usage**: one icon and one panel with a tab per usage source. The
[Venice](https://github.com/reneil1337/omarchy-venice-monitor) and
[LiteLLM](https://github.com/reneil1337/omarchy-litellm-monitor) monitors ship
the same panel, and either one's widget shows a tab for every record in
`~/.local/state/omarchy/agents/usage/` that has usage: a monitor's long
history, or an agent collector's seven-day window with real tokens. That
gives **Claude Code** (and any other agent with recent usage) its old
seven-day day-and-model charts back, which Omarchy 4.5 removed everywhere.
With both monitors installed **add only one AI Usage widget** to the bar:

```sh
omarchy bar put io.github.reneil1337.litellm --after omarchy.agents
```

- **Week · Month · Quarter · Year**, offered as far as a record's history
  reaches (30, 84, and 335 days for the longer ones; agents with only the
  seven-day window show Week with their model totals beside it).
  These are windows, not calendar periods: **Week** is the last seven days
  ending today (not Monday–Sunday), **Month** the last 30 days, **Quarter**
  13 Monday-aligned calendar weeks, **Year** 12 calendar months (from the 1st
  of the month 11 months back). The panel prints the exact dates under the
  range buttons, and every total is the sum of the days the chart draws.
- **Totals**: tokens, requests, and cost (tokens per day when a record has no
  cost), with the change against the window before it.
- **Balance**: remaining credits with a gauge that drains toward empty.
- **Tokens over time**: seven rows with today in bold, or columns for the
  longer ranges (days, calendar weeks, calendar months). Hover for details.
- **Tokens by model**: the top five with their share, renamed SKUs merged,
  and the input/output/cache split on hover in the week view.

| Input | Action |
|---|---|
| Click | Open or close the panel |
| Right click | Refresh every monitor |
| Middle click, ← / →, `1`–`9` | Switch tabs |
| `w` `m` `q` `y` | Week, month, quarter, year |
| `r` or Enter | Refresh the current tab |

```sh
omarchy-shell io.github.reneil1337.litellm.usage toggle
omarchy-shell io.github.reneil1337.litellm.usage provider litellm
omarchy-shell io.github.reneil1337.litellm.usage range month
```

The refresh button asks the right collector to run again: a monitor's
service over IPC, or `omarchy-agent-usage-update claude` for an agent.

The panel only reads records; it never writes. `UsagePanel.qml`,
`UsageModel.js`, `Providers.js`, and `i18n/` are identical in both monitor
repositories (only `Plugin.js` differs), so copy changes to both.

After installing or updating, run `omarchy restart shell`: the shell indexes
plugin files when it starts and rejects new ones until then (it reports `File
name case mismatch`). A widget that failed to load once is also dropped from
the bar; run the `bar put` again.

### Hide it from the agents panel

The stock agents panel reads the same record and would show a second LiteLLM
section (the 7-day heading) and count LiteLLM into its rotating token
summary. If you keep the AI Usage widget, turn the agents-panel copy off; the
record and the AI Usage tab are untouched:

```sh
omarchy bar set omarchy.agents providers '{"litellm":{"enabled":false}}' --json
```

Add `"venice":{"enabled":false}` alongside it if the Venice monitor is
installed too. Reverse with `true`.

## Configure

Edit `config.json` in the plugin folder:

```json
{
  "baseUrl": "http://your-litellm-host:4000",
  "apiKey": "sk-..."
}
```

`apiKey` must be able to *read* spend data. Recommended setup: create a
dedicated read-only user + key instead of pasting your master key.

In the LiteLLM admin UI: **Add user** → role **Admin (View Only)**
(`proxy_admin_viewer`) → then create a key for that user with **no team**
(a personal key inherits the view-only role). If that UI path is missing in
your version, mint it with your master key:

```bash
LITELLM_MASTER=sk-your-master-key

# 1. view-only admin user (result contains "user_id")
curl -sX POST http://your-litellm-host:4000/user/new \
  -H "Authorization: Bearer $LITELLM_MASTER" \
  -d '{"user_email":"dashboard@local.lan","user_role":"proxy_admin_viewer"}'

# 2. personal key owned by that user (result contains "key")
curl -sX POST http://your-litellm-host:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER" \
  -d '{"user_id":"usr-..."}'
```

Paste the resulting `sk-...` into `config.json` and then:

```sh
chmod 600 ~/.config/omarchy/plugins/io.github.reneil1337.litellm/config.json
```

The `LITELLM_API_KEY` environment variable overrides `apiKey` if you'd
rather not store it on disk.

## Usage

Once the collector has produced a record, the AI Usage panel gets a LiteLLM
tab showing tokens and requests for your router. Force a refresh sooner
than the 10-minute interval with:

```sh
omarchy-shell io.github.reneil1337.litellm refresh
```

## Requirements

- Omarchy 4.5+ shell with plugin support
- `python3` (stdlib only — the collector has no pip dependencies)
- A reachable LiteLLM proxy (tested against LiteLLM API 1.101.0) with
  `/user/daily/activity` available to the key

Day groupings follow your local timezone. Token totals use the LiteLLM
canonical `total_tokens`; if your models have no pricing configured in the
proxy, `$` spend stays zero, but the token views are unaffected.

## How it works

The collector writes `litellm.json` to
`~/.local/state/omarchy/agents/usage/` — the shared record directory the
Omarchy agents panel reads. The record carries `history` (per-day tokens +
requests for the last year) and `modelDaily` (per-model per-day tokens),
which the AI Usage panel charts, plus the agents-panel fields (`recentDays`,
`modelUsage`, `tierLabel`) the stock panel reads. Other tools can keep
feeding that same directory.

The plugin is strictly data-only: it writes exactly one file,
`litellm.json`, and reads nothing but its own `config.json` next to the
script — it never touches user configuration or other agents' records.

## Data volume

The proxy aggregates server-side: `GET /user/daily/activity` takes the whole
window in one query and returns per-day totals with a per-model breakdown, so
a full year is about seven requests and two seconds. Every refresh re-reads
the whole window (no local cache or state), which keeps late corrections in
the numbers and gives the panel Month, Quarter, and Year without any first
build. Venice Monitor, whose API only serves a per-entry ledger, caches day
aggregates instead.

## Checks

```sh
node --test tests/
omarchy plugin validate .
```

## Keep up to date

```sh
omarchy plugin update io.github.reneil1337.litellm
```

## Remove

```sh
omarchy plugin remove io.github.reneil1337.litellm
```

## License

MIT — see [LICENSE](LICENSE).
