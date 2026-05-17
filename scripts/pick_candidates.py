#!/usr/bin/env python3
"""raw_posts.json から「採点候補」を抽出して JSON Lines で stdout に出す。

Claude API は呼ばない。エンゲージメント順に並べて、上位 N 件を出すだけ。
採点とリライトは呼び出し側の Claude Code subagent が担当する。

使用例:
    python3 scripts/pick_candidates.py \
        --raw-root output/raw --target-date yesterday --pool-size 40
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone


def engagement_score(p: dict) -> int:
    return (
        p.get("likes", 0)
        + p.get("retweets", 0) * 3
        + p.get("quotes", 0) * 4
        + p.get("replies", 0) * 2
        + p.get("bookmarks", 0) * 2
    )


def flatten(raw: dict) -> list[dict]:
    flat = []
    for posts in raw.values():
        for p in posts:
            if p.get("is_reply") and not p.get("is_self_reply"):
                continue
            text = (p.get("text") or "").strip()
            if len(text) < 20:
                continue
            flat.append(p)
    return flat


def main():
    ap = argparse.ArgumentParser(description="raw_posts.json から候補を JSONL で出力")
    ap.add_argument("--raw-root", default="output/raw")
    ap.add_argument("--target-date", required=True, help="YYYY-MM-DD or 'yesterday' (JST)")
    ap.add_argument("--pool-size", type=int, default=40, help="出力する候補の上限")
    args = ap.parse_args()

    target_date = args.target_date
    if target_date == "yesterday":
        jst = timezone(timedelta(hours=9))
        target_date = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[target-date] yesterday → {target_date} (JST)", file=sys.stderr)

    raw_path = os.path.join(args.raw_root, target_date, "raw_posts.json")
    if not os.path.exists(raw_path):
        sys.exit(f"raw file not found: {raw_path}")

    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    flat = flatten(raw)
    flat.sort(key=engagement_score, reverse=True)
    candidates = flat[: args.pool_size]

    print(f"[pick] flatten={len(flat)} / picked={len(candidates)}", file=sys.stderr)

    for p in candidates:
        out = {
            "url": p.get("url"),
            "author": p.get("author"),
            "text": p.get("text", ""),
            "score": engagement_score(p),
            "likes": p.get("likes", 0),
            "retweets": p.get("retweets", 0),
            "replies": p.get("replies", 0),
            "media_urls": p.get("media_urls", []) or [],
        }
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
