#!/usr/bin/env python3
"""
rewrite_top.py が生成した posts.md と、その元になった raw_posts.json を
組み合わせて Slack Webhook に投稿する。

各リライト案を 1 メッセージにして:
- リライト本文
- 元投稿 URL
- メディア URL (画像は Slack 側でインライン展開される)

を投げる。10 件あれば 10 メッセージ。

使用例:
    python3 .claude/skills/x-slack-poster/scripts/notify_slack.py \
        --raw-root output/raw --rewrite-root output/rewrites --target-date yesterday

環境変数:
    SLACK_WEBHOOK_URL — Slack incoming webhook URL (必須)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone


def engagement_score(p: dict) -> int:
    return (
        p.get("likes", 0)
        + p.get("retweets", 0) * 3
        + p.get("quotes", 0) * 4
        + p.get("replies", 0) * 2
        + p.get("bookmarks", 0) * 2
    )


def find_webhook() -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        return url
    for p in [
        os.path.expanduser("~/.claude/skills/x-post-drafter/slack.json"),
        os.path.join(os.getcwd(), "config", "slack.json"),
    ]:
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
                u = cfg.get("webhook_url")
                if u and not u.startswith("PASTE"):
                    return u
            except (json.JSONDecodeError, OSError):
                continue
    sys.exit("SLACK_WEBHOOK_URL が設定されていません")


def post_slack(webhook: str, payload: dict) -> None:
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"slack returned {r.status}")


def parse_posts_md(md: str) -> list[dict]:
    """rewrite_top.py が出した posts.md を粗くパースして
    [{score, author, rewrite, source_url}, ...] を返す。"""
    items = []
    sections = re.split(r"^## ", md, flags=re.MULTILINE)[1:]
    for s in sections:
        header = s.splitlines()[0]
        m = re.match(r"\d+\.\s+score=(\d+)\s*/\s*(.+)", header)
        if not m:
            continue
        score = int(m.group(1))
        author = m.group(2).strip()
        rw_match = re.search(r"```\s*\n(.*?)\n```", s, re.DOTALL)
        rewrite = rw_match.group(1).strip() if rw_match else ""
        url_match = re.search(r"\*\*元投稿:\*\*\s*(\S+)", s)
        source_url = url_match.group(1) if url_match else ""
        items.append({"score": score, "author": author, "rewrite": rewrite, "url": source_url})
    return items


def find_source_post(raw: dict, url: str) -> dict | None:
    for posts in raw.values():
        for p in posts:
            if p.get("url") == url:
                return p
    return None


def build_blocks(item: dict, source: dict | None, idx: int, total: int) -> list[dict]:
    """Slack Block Kit でメッセージを組み立て。画像 URL はそのまま image ブロックで貼る。"""
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":bulb: *AI 副業リライト案 {idx}/{total}* "
                    f"(score={item['score']} / {item['author']})\n"
                    f"```\n{item['rewrite']}\n```"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"元投稿: <{item['url']}>"},
            ],
        },
    ]
    if source:
        media = source.get("media_urls", []) or []
        for url in media[:4]:
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE) or "pbs.twimg.com/media" in url:
                blocks.append({
                    "type": "image",
                    "image_url": url,
                    "alt_text": f"元投稿のメディア {url[-30:]}",
                })
            else:
                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f":movie_camera: メディア: <{url}>"}],
                })
    blocks.append({"type": "divider"})
    return blocks


def main():
    ap = argparse.ArgumentParser(description="rewrite_top.py の出力を Slack に通知")
    ap.add_argument("--raw-root", default="output/raw")
    ap.add_argument("--rewrite-root", default="output/rewrites")
    ap.add_argument("--target-date", required=True, help="YYYY-MM-DD または 'yesterday'(JST)")
    ap.add_argument("--dry-run", action="store_true", help="Slack に送らず stdout に dump")
    args = ap.parse_args()

    target_date = args.target_date
    if target_date == "yesterday":
        jst = timezone(timedelta(hours=9))
        target_date = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[target-date] yesterday → {target_date} (JST)", file=sys.stderr)

    md_path = os.path.join(args.rewrite_root, target_date, "posts.md")
    raw_path = os.path.join(args.raw_root, target_date, "raw_posts.json")
    for p in (md_path, raw_path):
        if not os.path.exists(p):
            sys.exit(f"missing: {p}")

    with open(md_path, encoding="utf-8") as f:
        items = parse_posts_md(f.read())
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    if not items:
        sys.exit(f"posts.md からリライト案を 1 件もパースできませんでした: {md_path}")

    webhook = "<dry-run>" if args.dry_run else find_webhook()

    intro = (
        f":memo: *AI 副業リライト案 {target_date} JST 分 ({len(items)} 件)* "
        f"\n以下を 1 日に分散して X に手動投稿してください。"
    )
    if args.dry_run:
        print("[intro]", intro)
    else:
        post_slack(webhook, {"text": intro})

    for i, item in enumerate(items, start=1):
        source = find_source_post(raw, item["url"])
        blocks = build_blocks(item, source, i, len(items))
        payload = {"text": f"リライト案 {i}/{len(items)} (score={item['score']})", "blocks": blocks}
        if args.dry_run:
            print(f"\n--- [{i}/{len(items)}] ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue
        try:
            post_slack(webhook, payload)
            print(f"  [{i}/{len(items)}] sent", file=sys.stderr)
        except Exception as e:
            print(f"  [{i}/{len(items)}] failed: {e}", file=sys.stderr)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
