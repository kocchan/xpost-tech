#!/usr/bin/env python3
"""subagent が生成したリライト結果を posts.md に書き出す。

入力 (--items-file or stdin): JSON
{
  "target_date": "YYYY-MM-DD",
  "items": [
    {
      "url": "...",
      "author": "...",
      "score": int,
      "media_urls": [...],
      "rewrite": "リライト本文",
      "relevance": 0-5,
      "quality": 0-5,
      "reason": "20字程度"
    }, ...
  ]
}

Claude API は呼ばない。整形して posts.md に書くだけ。

使用例:
    cat rewrites.json | python3 scripts/save_rewrite.py --rewrite-root output/rewrites
    python3 scripts/save_rewrite.py --items-file rewrites.json --rewrite-root output/rewrites
"""
import argparse
import json
import os
import sys


def build_markdown(target_date: str, items: list[dict]) -> str:
    lines = [
        f"# AI 副業リライト案 — {target_date} 分",
        "",
        f"元投稿 {len(items)} 件分のリライト案。各セクションを 1 ツイートとして手動投稿する想定。",
        "",
    ]
    for i, item in enumerate(items, start=1):
        cls_tag = ""
        if "relevance" in item or "quality" in item:
            cls_tag = (
                f" / r={item.get('relevance','?')} q={item.get('quality','?')}"
                f" ({item.get('reason','')})"
            )
        lines.append(
            f"## {i}. score={item.get('score','?')} / {item.get('author','?')}{cls_tag}"
        )
        lines.append("")
        lines.append("**リライト案:**")
        lines.append("```")
        lines.append(item.get("rewrite", ""))
        lines.append("```")
        lines.append("")
        lines.append(f"**元投稿:** {item.get('url', '?')}")
        media = item.get("media_urls") or []
        if media:
            lines.append("")
            lines.append("**元投稿のメディア (参考用):**")
            for u in media:
                lines.append(f"- {u}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="リライト結果を posts.md に保存")
    ap.add_argument("--rewrite-root", default="output/rewrites")
    ap.add_argument(
        "--items-file",
        help="JSON ファイルパス。未指定なら stdin から読む。",
    )
    args = ap.parse_args()

    if args.items_file:
        with open(args.items_file, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)

    target_date = payload.get("target_date")
    items = payload.get("items") or []
    if not target_date:
        sys.exit("target_date が必要です")
    if not items:
        sys.exit("items が空です")

    out_dir = os.path.join(args.rewrite_root, target_date)
    os.makedirs(out_dir, exist_ok=True)
    out_md = os.path.join(out_dir, "posts.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(build_markdown(target_date, items))
    print(f"[wrote] {out_md} ({len(items)} 件)", file=sys.stderr)


if __name__ == "__main__":
    main()
