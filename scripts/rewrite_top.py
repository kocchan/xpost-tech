#!/usr/bin/env python3
"""
x-collecter が出力した raw_posts.json から Top N のバズ投稿を選び、
Claude Sonnet 4.6 で「AI 副業」向けに完全リライトして
output/rewrites/<対象日>/posts.md に書き出す。

Slack 通知はしない(別スクリプト notify_slack.py の役割)。

使用例:
    # 昨日 JST の raw を Top 10 リライト
    python3 scripts/rewrite_top.py \
        --raw-root output/raw --rewrite-root output/rewrites --target-date yesterday --top 10

    # 特定日
    python3 scripts/rewrite_top.py \
        --raw-root output/raw --rewrite-root output/rewrites --target-date 2026-05-16 --top 10

環境変数:
    ANTHROPIC_API_KEY  — 必須
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """あなたは「AI 副業」ジャンルで X (旧 Twitter) アカウントを運営する日本人マーケターのライターです。

タスク:
他人のバズった投稿を「構成と訴求の型だけ参考にし、文面は完全に書き直した」案を 1 本だけ作る。

絶対ルール:
- 元投稿の文章を 1 文字でもコピーしない(語彙レベルで書き換える)
- 280 文字以内 (X の本文上限)
- ターゲット: AI 副業に興味がある会社員 / 副業初心者
- 数字フック・箇条書き・結論先出しなど、構造的な型は参考にしてよい
- 煽り訴求 (根拠なき「月100万」「誰でも稼げる」など) は使わない
- 改行と空行を活かして読みやすく
- ハッシュタグは入れない (X ではほぼ無効)

出力フォーマット:
本文だけ。前置きや見出しや番号は不要。"""

USER_PROMPT_TEMPLATE = """以下のバズった元投稿を参考に、AI 副業向けの完全書き直し案を 1 本だけ作って。

元投稿 (構成と訴求の型だけ参考):
---
{text}
---

元投稿スコア: {score} (likes={likes} / RT={retweets} / replies={replies})
元投稿 URL (検証用): {url}"""


def engagement_score(p: dict) -> int:
    return (
        p.get("likes", 0)
        + p.get("retweets", 0) * 3
        + p.get("quotes", 0) * 4
        + p.get("replies", 0) * 2
        + p.get("bookmarks", 0) * 2
    )


def pick_top(raw: dict, top_n: int) -> list[dict]:
    """raw_posts.json を flatten してスコア順に Top N。RT/reply は除外。"""
    flat = []
    for posts in raw.values():
        for p in posts:
            if p.get("is_retweet") or p.get("is_reply"):
                continue
            flat.append(p)
    flat.sort(key=engagement_score, reverse=True)
    return flat[:top_n]


def rewrite_one(client: anthropic.Anthropic, post: dict) -> str:
    user_msg = USER_PROMPT_TEMPLATE.format(
        text=post.get("text", ""),
        score=engagement_score(post),
        likes=post.get("likes", 0),
        retweets=post.get("retweets", 0),
        replies=post.get("replies", 0),
        url=post.get("url", "?"),
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    return next((b.text for b in response.content if b.type == "text"), "").strip()


def build_markdown(target_date: str, items: list[dict]) -> str:
    lines = [
        f"# AI 副業リライト案 — {target_date} 分",
        "",
        f"元投稿 {len(items)} 件分のリライト案。各セクションを 1 ツイートとして手動投稿する想定。",
        "",
    ]
    for i, item in enumerate(items, start=1):
        post = item["source"]
        media = post.get("media_urls", []) or []
        media_block = ""
        if media:
            media_block = "\n**元投稿のメディア (参考用):**\n" + "\n".join(f"- {u}" for u in media) + "\n"
        lines.append(f"## {i}. score={engagement_score(post)} / {post.get('author','?')}")
        lines.append("")
        lines.append("**リライト案:**")
        lines.append("```")
        lines.append(item["rewrite"])
        lines.append("```")
        lines.append("")
        lines.append(f"**元投稿:** {post.get('url', '?')}")
        if media_block:
            lines.append(media_block)
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Top N をリライトして posts.md に保存(Slack 通知なし)")
    ap.add_argument("--raw-root", default="output/raw", help="raw_posts.json のルート")
    ap.add_argument("--rewrite-root", default="output/rewrites", help="posts.md の出力ルート")
    ap.add_argument("--target-date", required=True, help="YYYY-MM-DD または 'yesterday'(JST)")
    ap.add_argument("--top", type=int, default=10, help="リライト対象の上位件数 (default: 10)")
    args = ap.parse_args()

    target_date = args.target_date
    if target_date == "yesterday":
        jst = timezone(timedelta(hours=9))
        target_date = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[target-date] yesterday → {target_date} (JST)", file=sys.stderr)

    raw_path = os.path.join(args.raw_root, target_date, "raw_posts.json")
    if not os.path.exists(raw_path):
        sys.exit(f"raw file not found: {raw_path}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 環境変数を設定してください")

    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)

    top = pick_top(raw, args.top)
    if not top:
        sys.exit(f"対象投稿がありません ({raw_path} に RT/reply 以外で 0 件)")

    print(f"[mode: model={MODEL} / top={len(top)} / target={target_date}]", file=sys.stderr)
    client = anthropic.Anthropic()

    items = []
    for i, post in enumerate(top, start=1):
        print(f"  [{i}/{len(top)}] {post.get('url')}", file=sys.stderr)
        try:
            rw = rewrite_one(client, post)
        except anthropic.APIError as e:
            print(f"    error: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        items.append({"source": post, "rewrite": rw})

    if not items:
        sys.exit("リライト 0 件。Anthropic API のエラーログを確認してください。")

    out_dir = os.path.join(args.rewrite_root, target_date)
    os.makedirs(out_dir, exist_ok=True)
    out_md = os.path.join(out_dir, "posts.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(build_markdown(target_date, items))
    print(f"[wrote] {out_md} ({len(items)} 件)", file=sys.stderr)


if __name__ == "__main__":
    main()
