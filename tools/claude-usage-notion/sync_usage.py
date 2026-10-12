#!/usr/bin/env python3
"""Anthropic Usage & Cost API -> Notion database sync (stdlib only).

Env vars:
  ANTHROPIC_ADMIN_KEY  Admin API key (sk-ant-admin...), org accounts only
  NOTION_TOKEN         Notion integration token (share the target page/DB with it)
  NOTION_DATABASE_ID   target DB id (printed by --setup)
  NOTION_TOTAL_PAGE_ID optional; page that gets an all-time-cost callout
  NOTION_BUDGET_USD    optional; total budget, adds "remaining" to the callout

Usage:
  python3 sync_usage.py --setup <parent_page_id>   # create the DB once
  python3 sync_usage.py                            # sync last 24h tokens + 3d cost
  python3 sync_usage.py --hours 72 --cost-days 7
  python3 sync_usage.py --dry-run
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ANTHROPIC = "https://api.anthropic.com/v1/organizations"
NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def http(method, url, headers, body=None, retries=5):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()[:500]}")


def anthropic_get(path, params):
    headers = {
        "x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"],
        "anthropic-version": "2023-06-01",
    }
    page = None
    while True:
        q = list(params)
        if page:
            q.append(("page", page))
        res = http("GET", f"{ANTHROPIC}/{path}?{urllib.parse.urlencode(q)}", headers)
        yield from res["data"]
        if not res.get("has_more"):
            return
        page = res["next_page"]


def notion(method, path, body=None):
    headers = {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    time.sleep(0.35)  # Notion limit is ~3 req/s
    return http(method, f"{NOTION}/{path}", headers, body)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def setup(parent_page_id):
    num = {"number": {"format": "number"}}
    db = notion("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Claude API 사용량"}}],
        "properties": {
            "Key": {"title": {}},
            "기간 시작": {"date": {}},
            "단위": {"select": {"options": [
                {"name": "hour", "color": "blue"}, {"name": "day", "color": "green"}]}},
            "모델": {"rich_text": {}},
            "입력 토큰": num,
            "캐시 쓰기 토큰": num,
            "캐시 읽기 토큰": num,
            "출력 토큰": num,
            "비용 (USD)": {"number": {"format": "dollar"}},
        },
    })
    print("Created. Set this and run the sync:")
    print(f"export NOTION_DATABASE_ID={db['id']}")


def existing_pages(db_id, since):
    """Map Key -> page id for rows at/after `since`."""
    out, cursor = {}, None
    while True:
        body = {"page_size": 100, "filter": {
            "property": "기간 시작", "date": {"on_or_after": iso(since)}}}
        if cursor:
            body["start_cursor"] = cursor
        res = notion("POST", f"databases/{db_id}/query", body)
        for p in res["results"]:
            title = p["properties"]["Key"]["title"]
            if title:
                out[title[0]["plain_text"]] = p["id"]
        if not res["has_more"]:
            return out
        cursor = res["next_cursor"]


def props(key, start, unit, model, tokens=None, cost=None):
    p = {
        "Key": {"title": [{"text": {"content": key}}]},
        "기간 시작": {"date": {"start": start}},
        "단위": {"select": {"name": unit}},
        "모델": {"rich_text": [{"text": {"content": model}}]},
    }
    if tokens:
        p["입력 토큰"] = {"number": tokens[0]}
        p["캐시 쓰기 토큰"] = {"number": tokens[1]}
        p["캐시 읽기 토큰"] = {"number": tokens[2]}
        p["출력 토큰"] = {"number": tokens[3]}
    if cost is not None:
        p["비용 (USD)"] = {"number": cost}
    return p


def collect_rows(hours, cost_days):
    now = datetime.now(timezone.utc)
    rows = []

    start = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    params = [("starting_at", iso(start)), ("ending_at", iso(now + timedelta(hours=1))),
              ("bucket_width", "1h"), ("group_by[]", "model"), ("limit", "168")]
    for bucket in anthropic_get("usage_report/messages", params):
        for r in bucket["results"]:
            cw = r.get("cache_creation") or {}
            tokens = (
                r.get("uncached_input_tokens", 0),
                cw.get("ephemeral_5m_input_tokens", 0) + cw.get("ephemeral_1h_input_tokens", 0),
                r.get("cache_read_input_tokens", 0),
                r.get("output_tokens", 0),
            )
            if not any(tokens):
                continue
            model = r.get("model") or "unknown"
            s = bucket["starting_at"]
            rows.append((f"hour|{s}|{model}", props(f"{s} | {model}", s, "hour", model, tokens=tokens)))

    cstart = (now - timedelta(days=cost_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    params = [("starting_at", iso(cstart)), ("ending_at", iso(now + timedelta(days=1))),
              ("bucket_width", "1d"), ("group_by[]", "description"), ("limit", "31")]
    # Cost API splits one model's daily cost into several lines (input/output/cache...),
    # so sum them per (day, model) before writing.
    costs = {}
    for bucket in anthropic_get("cost_report", params):
        for r in bucket["results"]:
            # amount is a decimal string in cents
            usd = float(r["amount"]) / 100
            model = r.get("model") or r.get("description") or "unknown"
            k = (bucket["starting_at"], model)
            costs[k] = costs.get(k, 0) + usd
    for (s, model), usd in costs.items():
        if usd:
            rows.append((f"day|{s}|{model}", props(f"{s[:10]} | {model} (cost)", s, "day", model, cost=round(usd, 4))))
    return rows, start


def sync(args):
    db_id = os.environ["NOTION_DATABASE_ID"]
    rows, start = collect_rows(args.hours, args.cost_days)
    print(f"{len(rows)} rows from Anthropic")
    if args.dry_run:
        for k, _ in rows:
            print(" ", k)
        return
    # Keys are stored as the Notion title; build the same title to look up.
    cost_start = (datetime.now(timezone.utc) - timedelta(days=args.cost_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)  # same floor as collect_rows
    oldest = min(start, cost_start)
    existing = existing_pages(db_id, oldest)
    created = updated = 0
    for _, p in rows:
        title = p["Key"]["title"][0]["text"]["content"]
        if title in existing:
            notion("PATCH", f"pages/{existing[title]}", {"properties": p})
            updated += 1
        else:
            notion("POST", "pages", {"parent": {"database_id": db_id}, "properties": p})
            created += 1
    print(f"created {created}, updated {updated}")
    update_total(db_id)


TOTAL_ICON = "💲"  # the callout is identified by its icon, since its text changes


def find_total_callout(block_id, depth=0):
    """Depth-first search for the icon callout, so it is found inside columns/toggles too."""
    cursor = None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = notion("GET", f"blocks/{block_id}/children{q}")
        for b in res["results"]:
            if b["type"] == "callout" and (b["callout"].get("icon") or {}).get("emoji") == TOTAL_ICON:
                return b["id"]
        if depth < 4:
            for b in res["results"]:
                if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
                    found = find_total_callout(b["id"], depth + 1)
                    if found:
                        return found
        if not res["has_more"]:
            return None
        cursor = res["next_cursor"]


def update_total(db_id):
    """Keep a callout on NOTION_TOTAL_PAGE_ID showing the all-time cost in USD.

    The callout is created once (appended at the bottom; drag it to the top by hand)
    and found again on later runs by its icon, so its position is preserved.
    """
    page_id = os.environ.get("NOTION_TOTAL_PAGE_ID")
    if not page_id:
        return
    total, cursor = 0.0, None
    while True:
        body = {"page_size": 100, "filter": {"property": "단위", "select": {"equals": "day"}}}
        if cursor:
            body["start_cursor"] = cursor
        res = notion("POST", f"databases/{db_id}/query", body)
        for p in res["results"]:
            total += p["properties"]["비용 (USD)"]["number"] or 0
        if not res["has_more"]:
            break
        cursor = res["next_cursor"]

    kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    amount = f"${total:,.2f}"
    budget = os.environ.get("NOTION_BUDGET_USD")
    if budget:
        amount += f" / ${float(budget):,.2f}"
    rich = [{"type": "text", "text": {"content": f"{amount}  (업데이트 {kst} KST)"}}]

    block_id = find_total_callout(page_id)

    if block_id:
        notion("PATCH", f"blocks/{block_id}", {"callout": {"rich_text": rich}})
    else:
        notion("PATCH", f"blocks/{page_id}/children", {"children": [
            {"object": "block", "type": "callout",
             "callout": {"rich_text": rich, "icon": {"type": "emoji", "emoji": TOTAL_ICON}}}]})
        print("Created the total callout at the bottom of the page; drag it to the top.")
    print(f"total cost: ${total:,.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", metavar="PARENT_PAGE_ID")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--cost-days", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    setup(a.setup) if a.setup else sync(a)
