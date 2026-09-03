#!/usr/bin/env python3
"""Collects usage data from a LiteLLM proxy and writes the record that the
stock omarchy.agents panel reads: ~/.local/state/omarchy/agents/usage/litellm.json

Config lives in config.json next to this script:
{
  "baseUrl": "http://host:4000",
  "apiKey": "sk-..."   (or leave empty and export LITELLM_API_KEY)
}

Requires no third-party dependencies (stdlib only).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DAYS = 7            # legacy recentDays window, drives "TOKENS BY DAY"
MONTHS = 4          # status-line roll-up (total tokens + requests)
HISTORY_DAYS = 365  # full per-day history kept in the record (schema "weekday")
PAGE_SIZE = 400
MAX_PAGES = 32
STATE_SUBDIR = "omarchy/agents/usage"
RECORD_ID = "litellm"


def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    base = str(cfg.get("baseUrl", "")).rstrip("/")
    key = str(cfg.get("apiKey", "")).strip() or os.environ.get("LITELLM_API_KEY", "").strip()
    return base, key


def api_get(base, key, path, params, retries=3):
    qs = urllib.parse.urlencode(params)
    url = base + path + ("?" + qs if qs else "")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("auth failed: this key cannot read spend data (needs admin:read scope)")
            if 400 <= e.code < 500:
                raise RuntimeError("HTTP %s for %s: %s" % (e.code, path, e.read()[:300]))
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries - 1:
                raise
            time.sleep(2 + attempt * 2)


def utc_day(ts, tz_offset):
    """Group timestamps into local calendar days (input: epoch seconds)."""
    return time.strftime("%Y-%m-%d", time.gmtime(ts + tz_offset))


def local_tz_offset(now):
    """Offset to apply in utc_day(): seconds ahead of UTC for local time."""
    local = now + (time.altzone if time.daylight and time.localtime(now).tm_isdst else time.timezone)
    return local - now


def tz_minutes_west(now):
    """Minutes west of UTC, the litellm daily-activity convention (480 for PST)."""
    seconds = time.altzone if time.daylight and time.localtime(now).tm_isdst else time.timezone
    return int(seconds / 60)


def date_strings(days, now):
    out = []
    for offset in range(days - 1, -1, -1):
        out.append(utc_day(now - offset * 86400, local_tz_offset(now)))
    return out


def fetch_activity(base, key, now):
    """Per-day totals and per-model breakdown for the full history window,
    from /user/daily/activity (server-side aggregation, a handful of pages)."""
    offset_secs = local_tz_offset(now)
    start = utc_day(now - (HISTORY_DAYS - 1) * 86400, offset_secs)
    end = utc_day(now, offset_secs)
    params = {
        "start_date": start,
        "end_date": end,
        "timezone": tz_minutes_west(now),
        "include_current_utc_day": "true",
    }

    days = {}          # date -> {tokens, requests}
    model_daily = {}   # model -> {date: tokens}
    model_parts = {}   # model -> date -> {input, output, cacheRead, cacheWrite}

    for page in range(1, MAX_PAGES + 1):
        data = api_get(base, key, "/user/daily/activity", dict(params, page=page, page_size=PAGE_SIZE))
        if not isinstance(data, dict) or not data.get("results"):
            break
        for row in data["results"]:
            day = str(row.get("date") or "")
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            breakdown_models = row.get("breakdown", {}).get("models", {}) if isinstance(row.get("breakdown"), dict) else {}
            tokens = int_or_zero(metrics.get("total_tokens"))
            requests = int_or_zero(metrics.get("api_requests"))
            days[day] = {"tokens": tokens, "requests": requests}
            for model, mini in breakdown_models.items():
                mmetrics = mini.get("metrics") if isinstance(mini.get("metrics"), dict) else {}
                mtotal = int_or_zero(mmetrics.get("total_tokens"))
                if mtotal == 0:
                    continue
                model_daily.setdefault(model, {})[day] = mtotal
                parts = model_parts.setdefault(model, {}).setdefault(day, {
                    "inputTokens": 0, "outputTokens": 0,
                    "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                })
                parts["inputTokens"] += int_or_zero(mmetrics.get("prompt_tokens"))
                parts["outputTokens"] += int_or_zero(mmetrics.get("completion_tokens"))
                parts["cacheReadInputTokens"] += int_or_zero(mmetrics.get("cache_read_input_tokens"))
                parts["cacheCreationInputTokens"] += int_or_zero(mmetrics.get("cache_creation_input_tokens"))
        meta = data.get("metadata") or {}
        if meta.get("has_more") is not True:
            break
        if page == MAX_PAGES:
            print("warning: hit page cap; history is partial", file=sys.stderr)

    fellows = date_strings(HISTORY_DAYS, now)
    return fellows, days, model_daily, model_parts


def int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def model_usage_window(model_parts, window):
    """Per-model component sums over a window of dates. The stock agents
    panel totals a model row as input + output + cache reads/writes and
    scales the table to the heaviest row."""
    usage = {}
    for model, per_day in model_parts.items():
        bucket = {"inputTokens": 0, "outputTokens": 0,
                  "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
        for day in window:
            parts = per_day.get(day)
            if parts:
                for field in bucket:
                    bucket[field] += int(parts.get(field, 0))
        if sum(bucket.values()) > 0:
            usage[model] = bucket
    return usage


def compact_tokens(count):
    value = float(count)
    for unit, divisor in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if value >= divisor:
            trimmed = round(value / divisor, 0 if value >= 10 * divisor else 1)
            return ("%g" % trimmed) + unit
    return "%d" % count


def build_record(base, key, now):
    fellows = fetch_activity(base, key, now)
    days_expected, days, model_daily, model_parts = fellows
    today = days_expected[-1]

    # Explicit zero-fill keeps the timeline contiguous for any window slice.
    history = []
    requests_total = 0
    for day in days_expected:
        info = days.get(day) or {"tokens": 0, "requests": 0}
        history.append({"date": day, "tokens": int(info["tokens"]), "requests": int(info["requests"])})
        requests_total += int(info["requests"])

    today_info = days.get(today) or {"tokens": 0, "requests": 0}

    model_daily_compact = {}
    for model, per_day in model_daily.items():
        rows = [[day, int(per_day[day])] for day in days_expected if per_day.get(day)]
        if rows:
            model_daily_compact[model] = rows

    recent_days = [{"date": day["date"], "messageCount": day["tokens"]} for day in history[-DAYS:]]

    # The model table must match the chart above it: same 7 days, same scale.
    model_usage_7d = model_usage_window(model_parts, days_expected[-DAYS:])

    # The panel can't render multi-month charts natively, so roll the last
    # four calendar months up into the status line instead.
    month_totals = {}
    for day in history:
        month = day["date"][:7]
        totals = month_totals.setdefault(month, {"tokens": 0, "requests": 0})
        totals["tokens"] += day["tokens"]
        totals["requests"] += day["requests"]
    months = sorted(month_totals)[-MONTHS:]
    month_tokens = sum(month_totals[m]["tokens"] for m in months)
    month_requests = sum(month_totals[m]["requests"] for m in months)

    record = {
        "id": RECORD_ID,
        "name": "LiteLLM",
        "ready": True,
        "scope": "account",
        "hasPromptStats": True,
        "tierLabel": "Router",
        "usageStatusText": (
            "4-month total: " + compact_tokens(month_tokens) + " tokens · "
            + compact_tokens(month_requests) + " requests"
            if month_tokens > 0 else ""
        ),
        "authHelpText": "",
        "todayPrompts": int(today_info["requests"]),
        "todaySessions": 0,
        "todayTotalTokens": int(today_info["tokens"]),
        "todayTokensByModel": {},
        "recentDays": recent_days,
        "totalPrompts": requests_total,
        "totalSessions": 0,
        "activeDays": sum(1 for day in history if day["tokens"] > 0),
        "modelUsage": model_usage_7d,
        "history": history,
        "modelDaily": model_daily_compact,
        "limits": [],
        "updatedAt": int(now * 1000),
    }
    return record


def write_record(record, state_base):
    path = os.path.join(state_base, STATE_SUBDIR, RECORD_ID + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    os.write(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
             (json.dumps(record, indent=1) + "\n").encode("utf-8"))
    os.replace(tmp, path)
    return path


def main():
    config_path = os.path.join(script_dir(), "config.json")
    try:
        base, key = load_config(config_path)
    except FileNotFoundError:
        print("config.json not found next to collector.py", file=sys.stderr)
        return 1
    if not base or not key:
        print("No baseUrl/apiKey configured yet.", file=sys.stderr)
        return 1

    now = time.time()
    state_base = os.path.join(os.environ.get("XDG_STATE_HOME") or
                              os.path.join(os.path.expanduser("~"), ".local/state"))
    try:
        record = build_record(base, key, now)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as e:
        print("Failed to reach LiteLLM proxy: %s" % e, file=sys.stderr)
        return 1

    write_record(record, state_base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
