#!/usr/bin/env python3
"""
posts.md と raw_posts.json を組み合わせて Slack に投稿する。

2 モード:
  (A) Bot Token モード (推奨): SLACK_BOT_TOKEN + SLACK_CHANNEL_ID が設定されている場合、
      chat.postMessage を使って「メインツイートを親メッセージ + スレッド継続を
      Slack スレッド返信」として投稿する。X にスレッド投稿する際の手動コピペが楽。

  (B) Webhook モード (フォールバック): SLACK_WEBHOOK_URL のみの場合、
      1 リライト = 1 メッセージで投稿する (スレッド機能は使えない)。

使用例:
    python3 scripts/notify_slack.py \\
        --raw-root output/raw --rewrite-root output/rewrites --target-date yesterday

環境変数:
    SLACK_BOT_TOKEN  — xoxb- で始まる Bot Token (Bot モード時に必須)
    SLACK_CHANNEL_ID — 投稿先チャンネル ID (例: C01234567。Bot モード時に必須)
    SLACK_WEBHOOK_URL — Incoming Webhook URL (Bot Token が無いとき使う)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from _env import load_dotenv

load_dotenv()  # ローカル実行時に .env を読む。GitHub Actions では Secrets が来るので no-op


def engagement_score(p: dict) -> int:
    return (
        p.get("likes", 0)
        + p.get("retweets", 0) * 3
        + p.get("quotes", 0) * 4
        + p.get("replies", 0) * 2
        + p.get("bookmarks", 0) * 2
    )


def find_webhook() -> str | None:
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
    return None


def post_webhook(webhook: str, payload: dict) -> None:
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"slack returned {r.status}")


def post_bot(token: str, channel: str, text: str, blocks: list[dict] | None = None,
             thread_ts: str | None = None) -> str:
    """chat.postMessage を叩いて ts (= スレッド親 ID) を返す。"""
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {body.get('error')} / {body}")
    return body["ts"]


def parse_posts_md(md: str) -> list[dict]:
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


def split_thread(rewrite: str) -> list[str]:
    """rewrite を "---" 区切りで分割し、空セグメントを除いて返す。"""
    return [p.strip() for p in re.split(r"\n\s*---\s*\n", rewrite) if p.strip()]


def build_parent_blocks(item: dict, source: dict | None, main_text: str,
                        idx: int, total: int, threaded: bool) -> tuple[str, list[dict]]:
    """親メッセージ (メインツイート) の blocks を組み立て。
    threaded=True ならスレッド誘導文言を入れる。
    戻り値: (fallback text, blocks)"""
    header = (
        f":bulb: *AI 副業リライト案 {idx}/{total}* "
        f"(score={item['score']} / {item['author']})"
    )
    if threaded:
        header += "\n_↓ スレッド継続は返信欄に投稿します。順番にコピーして X のスレッドに貼ってください_"

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{main_text}\n```"},
        },
    ]
    # 動画は親メッセージの下に
    if source:
        media = source.get("media_urls", []) or []
        for url in media[:4]:
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE) or "pbs.twimg.com/media" in url:
                blocks.append({
                    "type": "image",
                    "image_url": url,
                    "alt_text": f"元投稿のメディア {url[-30:]}",
                })
        videos = source.get("video_urls", []) or []
        if videos:
            video_lines = "\n".join(f":movie_camera: 動画 mp4: <{u}>" for u in videos[:4])
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": video_lines}],
            })
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"元投稿: <{item['url']}>"},
        ],
    })
    blocks.append({"type": "divider"})
    return f"リライト {idx}/{total} メイン (score={item['score']})", blocks


def build_thread_blocks(part_text: str, i: int, n: int) -> tuple[str, list[dict]]:
    """スレッド継続用 blocks。"""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*スレッド {i}/{n}*"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{part_text}\n```"},
        },
    ]
    return f"スレッド {i}/{n}", blocks


def build_webhook_blocks(item: dict, source: dict | None, idx: int, total: int) -> list[dict]:
    """Webhook モード用: 1 メッセージにメイン + 全スレッドを並べる従来形式。"""
    parts = split_thread(item["rewrite"])
    header = (
        f":bulb: *AI 副業リライト案 {idx}/{total}* "
        f"(score={item['score']} / {item['author']})"
    )
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
    ]
    for i, part in enumerate(parts, start=1):
        label = "*メインツイート*" if i == 1 else f"*スレッド {i}/{len(parts)}*"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{label}\n```\n{part}\n```"},
        })
    # 動画 + 元投稿
    if source:
        for url in (source.get("media_urls", []) or [])[:4]:
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE) or "pbs.twimg.com/media" in url:
                blocks.append({"type": "image", "image_url": url, "alt_text": f"元投稿のメディア {url[-30:]}"})
        videos = source.get("video_urls", []) or []
        if videos:
            video_lines = "\n".join(f":movie_camera: 動画 mp4: <{u}>" for u in videos[:4])
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": video_lines}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"元投稿: <{item['url']}>"}]})
    blocks.append({"type": "divider"})
    return blocks


def main():
    ap = argparse.ArgumentParser(description="posts.md を Slack に投稿 (Bot Token なら親 + スレッド返信)")
    ap.add_argument("--raw-root", default="output/raw")
    ap.add_argument("--rewrite-root", default="output/rewrites")
    ap.add_argument("--target-date", required=True, help="YYYY-MM-DD or 'yesterday' (JST)")
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

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    bot_channel = os.environ.get("SLACK_CHANNEL_ID")
    use_bot = bool(bot_token and bot_channel) and not args.dry_run

    if use_bot:
        mode = "bot (thread)"
    elif args.dry_run:
        mode = "dry-run"
    else:
        mode = "webhook (single message)"
    print(f"[mode: {mode} / items={len(items)} / target={target_date}]", file=sys.stderr)

    intro = (
        f":memo: *AI 副業リライト案 {target_date} JST 分 ({len(items)} 件)* "
        f"\nメイン投稿はメッセージ本文、スレッド継続は各メッセージのスレッド返信欄にあります。順番にコピーして X に貼ってください。"
    )

    webhook = None
    if not use_bot and not args.dry_run:
        webhook = find_webhook()
        if not webhook:
            sys.exit("SLACK_BOT_TOKEN+SLACK_CHANNEL_ID も SLACK_WEBHOOK_URL も設定されていません")

    if args.dry_run:
        print("[intro]", intro)
    elif use_bot:
        post_bot(bot_token, bot_channel, intro)
    else:
        post_webhook(webhook, {"text": intro})

    for i, item in enumerate(items, start=1):
        source = find_source_post(raw, item["url"])
        parts = split_thread(item["rewrite"])
        main_text = parts[0] if parts else item["rewrite"]
        continuations = parts[1:] if len(parts) > 1 else []

        if args.dry_run:
            print(f"\n--- [{i}/{len(items)}] parent ---")
            fb, blocks = build_parent_blocks(item, source, main_text, i, len(items), threaded=bool(continuations))
            print(json.dumps({"text": fb, "blocks": blocks}, ensure_ascii=False, indent=2))
            for j, p in enumerate(continuations, start=2):
                print(f"\n--- [{i}/{len(items)}] thread {j}/{len(parts)} ---")
                fb, blocks = build_thread_blocks(p, j, len(parts))
                print(json.dumps({"text": fb, "blocks": blocks}, ensure_ascii=False, indent=2))
            continue

        try:
            if use_bot:
                fb, parent_blocks = build_parent_blocks(item, source, main_text, i, len(items),
                                                       threaded=bool(continuations))
                ts = post_bot(bot_token, bot_channel, fb, parent_blocks)
                for j, p in enumerate(continuations, start=2):
                    fb_t, t_blocks = build_thread_blocks(p, j, len(parts))
                    post_bot(bot_token, bot_channel, fb_t, t_blocks, thread_ts=ts)
                    time.sleep(0.4)
                print(f"  [{i}/{len(items)}] sent (parent + {len(continuations)} replies)", file=sys.stderr)
            else:
                blocks = build_webhook_blocks(item, source, i, len(items))
                post_webhook(webhook, {"text": f"リライト案 {i}/{len(items)} (score={item['score']})", "blocks": blocks})
                print(f"  [{i}/{len(items)}] sent (single message)", file=sys.stderr)
        except Exception as e:
            print(f"  [{i}/{len(items)}] failed: {e}", file=sys.stderr)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
