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

from _env import load_dotenv

load_dotenv()  # ローカル実行時に .env を読む。GitHub Actions では Secrets が来るので no-op

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

# 関連度 / 質を判定するモデル。Sonnet 4.6 は Haiku より AI 副業 vs 周辺ニュース
# のニュアンス判定が安定するため採用。日 80 件で約 3 円。
CLASSIFY_MODEL = "claude-sonnet-4-6"
CLASSIFY_MAX_TOKENS = 256

CLASSIFY_SYSTEM = """あなたは「AI 副業」ジャンルの X 運用アシスタント。元ツイートを 2 軸で採点する。

# relevance (0-5) — AI 副業との関連度
- 5: AI ツールで副業 / フリーランス / 収益化した具体例・手順・実績
- 4: AI 副業者向けの心得・マインドセット・始め方
- 3: AI による業務効率化(副業に転用できる温度感)
- 2: AI モデル/ニュース紹介(副業との接点は薄い)
- 1: AI 以外のキャリア・副業・自己啓発
- 0: 完全に無関係(雑談・愚痴・個人ニュース)

# quality (0-5) — リライト素材としての質
- 5: 強いフック + 具体例(数字 / ステップ / 比較) + 結論。型として優秀
- 4: 主張と具体性が両立している
- 3: 平均的な発信
- 2: 抽象的で具体例が薄い、文章として弱い
- 1: 宣伝 / 告知 / 露骨な煽り / フォロー誘導のみ
- 0: 断片 / 文字化け / 意味不明

# 出力
1 行の JSON のみ。前置きやコードフェンスは禁止。
{"relevance": <int>, "quality": <int>, "reason": "<20字程度の理由>"}"""

CLASSIFY_USER_TEMPLATE = """次のツイートを採点:
---
{text}
---"""

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


def _flatten(raw: dict) -> list[dict]:
    """raw を flatten してフィルタ前の候補リストを返す。

    - 他人へのリプライは除外(会話断片はリライト素材にならない)
    - 本文が空 / 数文字しかないものは除外
    - RT・セルフリプライ・引用はリライト対象として残す
    """
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


def classify_post(client: anthropic.Anthropic, post: dict) -> dict:
    """Haiku 4.5 で relevance / quality を採点。失敗時は 0/0。"""
    try:
        response = client.messages.create(
            model=CLASSIFY_MODEL,
            max_tokens=CLASSIFY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": CLASSIFY_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": CLASSIFY_USER_TEMPLATE.format(text=post.get("text", "")),
                }
            ],
        )
        text = next((b.text for b in response.content if b.type == "text"), "").strip()
        # 念のためコードフェンス対応
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except (anthropic.APIError, json.JSONDecodeError, ValueError) as e:
        return {"relevance": 0, "quality": 0, "reason": f"parse-error: {type(e).__name__}"}


def pick_top(
    raw: dict,
    top_n: int,
    client: anthropic.Anthropic | None = None,
    min_relevance: int = 3,
    min_quality: int = 3,
) -> list[dict]:
    """エンゲージメント順 → Haiku 4.5 で関連度+質を採点 → 閾値以上を Top N。

    client=None ならフィルタ無しでスコア順 Top N(後方互換)。
    候補は cost 抑制のため上位 max(top_n*4, 40) に絞ってから採点。
    """
    flat = _flatten(raw)
    flat.sort(key=engagement_score, reverse=True)

    if client is None:
        return flat[:top_n]

    # Haiku 4.5 のコストは 80 件で 1 円未満なので、候補は思い切って全件まで広げる。
    # 上限を設けるのは「明らかに低エンゲージで採点する価値がない」帯を切るとき用。
    pool_size = max(top_n * 10, len(flat))
    candidates = flat[:pool_size]
    print(
        f"[classify] {len(candidates)} 件 → Haiku 4.5 で relevance>={min_relevance} / quality>={min_quality} を抽出",
        file=sys.stderr,
    )

    kept: list[dict] = []
    for p in candidates:
        cls = classify_post(client, p)
        p["_classification"] = cls
        rel = int(cls.get("relevance", 0) or 0)
        qua = int(cls.get("quality", 0) or 0)
        tag = f"r={rel} q={qua}"
        if rel >= min_relevance and qua >= min_quality:
            kept.append(p)
            print(f"  [keep] @{p.get('author')} {tag} ({cls.get('reason')})", file=sys.stderr)
        else:
            print(f"  [drop] @{p.get('author')} {tag} ({cls.get('reason')})", file=sys.stderr)
        if len(kept) >= top_n:
            break

    return kept[:top_n]


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
        cls = post.get("_classification") or {}
        cls_tag = ""
        if cls:
            cls_tag = f" / r={cls.get('relevance','?')} q={cls.get('quality','?')} ({cls.get('reason','')})"
        lines.append(f"## {i}. score={engagement_score(post)} / {post.get('author','?')}{cls_tag}")
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
    ap.add_argument("--min-relevance", type=int, default=3, help="AI 副業関連度の最低スコア 0-5 (default: 3)")
    ap.add_argument("--min-quality", type=int, default=3, help="素材としての質の最低スコア 0-5 (default: 3)")
    ap.add_argument(
        "--no-classify",
        action="store_true",
        help="関連度/質フィルタを無効化(従来のスコア順 Top N に戻す)",
    )
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

    client = anthropic.Anthropic()
    top = pick_top(
        raw,
        args.top,
        client=None if args.no_classify else client,
        min_relevance=args.min_relevance,
        min_quality=args.min_quality,
    )
    if not top:
        sys.exit(
            f"対象投稿がありません ({raw_path} に閾値以上のものなし。--min-relevance / --min-quality を下げて再試行)"
        )

    print(f"[mode: model={MODEL} / top={len(top)} / target={target_date}]", file=sys.stderr)

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
