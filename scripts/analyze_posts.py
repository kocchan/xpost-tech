#!/usr/bin/env python3
"""
fetch_x_posts.py の出力を入力に、アカウント別の人気投稿レポート(マークダウン)を生成する。

出力(マークダウン)内容:
- 横断 Top ランキング
- アカウントごとに:
  - どの投稿が人気か(エンゲージメントスコア順)
  - その投稿はどういう投稿か(本文 / フォーマット / 文字数 / 構造的特徴)
  - なんで人気なのか(構造的特徴から推定したヒューリスティック)
  - 情報ソース(元投稿の URL)
- 補助的な集計(投稿時間帯 JST ヒストグラム / フォーマット内訳 / 投稿頻度)

使用例:
    python analyze_posts.py --from-dir output/raw --out-dir output/analysis --top 10
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone


def parse_time(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        # Apify がたまに返す "Wed Apr 03 12:34:56 +0000 2024" 形式
        try:
            return datetime.strptime(str(s), "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None


def engagement_score(p: dict) -> int:
    """重み付きエンゲージメントスコア。
    リツイート/引用は『拡散』なので重く、リプライは『議論』、ブックマークは『保存価値』。
    Likes は基準値。"""
    return (
        p.get("likes", 0) * 1
        + p.get("retweets", 0) * 3
        + p.get("quotes", 0) * 4
        + p.get("replies", 0) * 2
        + p.get("bookmarks", 0) * 2
    )


def engagement_rate(p: dict) -> float | None:
    """インプレッション(views)に対するエンゲージメント率。views が 0 なら None。"""
    views = p.get("views", 0)
    if views <= 0:
        return None
    eng = p.get("likes", 0) + p.get("retweets", 0) + p.get("replies", 0) + p.get("quotes", 0)
    return round(eng / views * 100, 2)


def format_label(p: dict) -> str:
    if p.get("is_retweet"):
        return "retweet"
    if p.get("is_quote"):
        return "quote"
    if p.get("is_reply"):
        return "reply"
    if p.get("media_urls"):
        return "with_media"
    return "text_only"


def length_bucket(text: str) -> str:
    n = len(text or "")
    if n <= 60:
        return "short(<=60)"
    if n <= 140:
        return "mid(61-140)"
    return "long(>140)"


def analyze_account(handle: str, posts: list[dict], top_n: int) -> dict:
    if not posts:
        return {"handle": handle, "post_count": 0, "note": "no posts"}

    # 時刻パース済みリスト(時刻取れたものだけ)
    timed = []
    for p in posts:
        t = parse_time(p.get("time"))
        if t:
            timed.append((t, p))

    # 時間帯ヒストグラム(JST に変換: UTC + 9h は手抜きせず timezone で)
    from datetime import timedelta
    JST = timezone(timedelta(hours=9))
    hour_hist = Counter()
    weekday_hist = Counter()
    for t, _ in timed:
        local = t.astimezone(JST)
        hour_hist[local.hour] += 1
        weekday_hist[local.strftime("%a")] += 1

    # 投稿頻度
    post_per_day = None
    if len(timed) >= 2:
        timed.sort(key=lambda x: x[0])
        span_days = max((timed[-1][0] - timed[0][0]).total_seconds() / 86400, 1)
        post_per_day = round(len(timed) / span_days, 2)

    # フォーマット内訳
    fmt_hist = Counter(format_label(p) for p in posts)
    len_hist = Counter(length_bucket(p.get("text", "")) for p in posts)

    # Top N(エンゲージメント順)
    ranked = sorted(posts, key=engagement_score, reverse=True)
    top = []
    for p in ranked[:top_n]:
        top.append({
            "url": p.get("url"),
            "time": p.get("time"),
            "score": engagement_score(p),
            "engagement_rate_pct": engagement_rate(p),
            "likes": p.get("likes", 0),
            "retweets": p.get("retweets", 0),
            "replies": p.get("replies", 0),
            "quotes": p.get("quotes", 0),
            "views": p.get("views", 0),
            "bookmarks": p.get("bookmarks", 0),
            "format": format_label(p),
            "length": len(p.get("text", "")),
            "text": p.get("text", ""),
            # detect_info_source / detect_features が必要とするフィールドを保持
            "author": p.get("author") or f"@{handle}",
            "is_reply": bool(p.get("is_reply")),
            "is_quote": bool(p.get("is_quote")),
            "is_retweet": bool(p.get("is_retweet")),
            # 詳細ソース情報(新フィールド、ない投稿は欠落許容)
            "quoted_url": p.get("quoted_url"),
            "in_reply_to_url": p.get("in_reply_to_url"),
            "in_reply_to_screen": p.get("in_reply_to_screen"),
            "retweeted_url": p.get("retweeted_url"),
            "retweeted_screen": p.get("retweeted_screen"),
            "urls_in_text": p.get("urls_in_text", []),
        })

    # スコア統計
    scores = [engagement_score(p) for p in posts]

    return {
        "handle": handle,
        "post_count": len(posts),
        "post_per_day": post_per_day,
        "engagement_score": {
            "median": statistics.median(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "mean": round(statistics.mean(scores), 1) if scores else 0,
        },
        "format_breakdown": dict(fmt_hist),
        "length_breakdown": dict(len_hist),
        "hour_histogram_jst": {str(h): hour_hist.get(h, 0) for h in range(24)},
        "weekday_histogram_jst": dict(weekday_hist),
        "top_posts": top,
    }


def cross_account_top(by_account: dict, top_n: int) -> list[dict]:
    """全アカウント横断のTop N(同じ重み付けスコアで)"""
    flat = []
    for handle, posts in by_account.items():
        for p in posts:
            flat.append({
                "handle": handle,
                "url": p.get("url"),
                "time": p.get("time"),
                "score": engagement_score(p),
                "engagement_rate_pct": engagement_rate(p),
                "likes": p.get("likes", 0),
                "retweets": p.get("retweets", 0),
                "format": format_label(p),
                "text": p.get("text", ""),
            })
    flat.sort(key=lambda x: x["score"], reverse=True)
    return flat[:top_n]


def _today_jst_str() -> str:
    from datetime import timedelta
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d")


# ─────────────────────────────────────────
# 「なんで人気なのか」を構造的特徴から推定
# ─────────────────────────────────────────
def detect_features(text: str, fmt: str, length: int) -> list[str]:
    """投稿テキストから読み取れる構造的特徴(タグ)を返す。"""
    features: list[str] = []
    head = text[:80] if text else ""

    # 数字フック: 冒頭付近に具体的な数字が登場
    if re.search(r"\d+\s*(万|億|円|%|％|倍|回|分|時間|日|名|人|社|件|個)", head):
        features.append("数字フック(具体数値)")
    elif re.match(r"^\s*\d+", text or ""):
        features.append("数字フック(冒頭)")

    # 箇条書き構成
    if re.search(r"(?m)^\s*(\d+[\.\)、]|[①-⑩]|[・▼▶◯●]|-\s)", text or ""):
        features.append("箇条書き構成")

    # 結論先出し
    if re.search(r"(結論[:：]|つまり[:：]|要するに|ポイント[:：]|ひとこと[:：])", text or ""):
        features.append("結論先出し")

    # 短文/長文
    if length <= 60:
        features.append("短文インパクト")
    elif length > 200:
        features.append("ノウハウ網羅型(長文)")

    # メディア
    if fmt == "with_media":
        features.append("ビジュアル訴求(画像/動画あり)")

    # 逆張り/問題提起
    if re.search(r"(やめろ|間違ってる|オワコン|時代遅れ|嘘|本当は|ダメ|終わり|逆に)", text or ""):
        features.append("逆張り/問題提起")

    # 体験談/一次情報
    if re.search(r"(実際に|やってみた|うちで|自分の|わたしが|私が|月収|年収|売上)", text or ""):
        features.append("体験談/一次情報")

    # 公開・暴露フック
    if re.search(r"(公開します|全部書きます|暴露|裏側|内訳|まとめました|告白)", text or ""):
        features.append("公開/暴露フック")

    # 告知系(イベント・募集)
    if re.search(r"(本日|今日|明日|時から|参加|募集|締切|限定|無料相談)", text or ""):
        features.append("告知/イベント")

    return features


# ─────────────────────────────────────────
# 情報ソースの推定
# ─────────────────────────────────────────
def detect_info_source(post: dict) -> str:
    """投稿テキスト + raw 側の構造情報から、情報源を可能な限り具体的に推定する。
    例:
      - "引用RT元: https://x.com/anthropic/status/..."
      - "返信先: @user (https://x.com/user/status/...)"
      - "RT元: @claudeai (https://x.com/claudeai/status/...)"
      - "外部リンク: https://www.anthropic.com/news/..."
      - "本文中の参照: https://t.co/xyz (要訪問)"
      - "引用符で囲まれた発言: 「...」 (出典は本文要確認)"
    何も検出されなければ '独自ツイート(ソースなし、推測手がかりなし)' を返す。"""
    text = post.get("text") or ""
    sources: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        if label not in seen:
            seen.add(label)
            sources.append(label)

    # 1a. 引用 RT(構造情報優先)
    if post.get("is_quote"):
        if post.get("quoted_url"):
            add(f"引用RT元: {post['quoted_url']}")
        else:
            add("他ツイートの引用RT(URL 不明)")

    # 1b. 返信(構造情報優先)
    if post.get("is_reply"):
        target = post.get("in_reply_to_url")
        screen = post.get("in_reply_to_screen")
        if target:
            add(f"返信先: @{screen} ({target})")
        elif screen:
            add(f"返信先: @{screen}")
        else:
            add("他ツイートへの返信(対象不明)")

    # 1c. RT(構造情報優先)
    if post.get("is_retweet"):
        if post.get("retweeted_url"):
            add(f"RT元: @{post.get('retweeted_screen','?')} ({post['retweeted_url']})")
        else:
            add("他ツイートの RT(元 URL 不明)")

    # 2. 外部 URL(X 以外、最優先)
    seen_urls = set()
    # 構造情報の urls_in_text を最優先(t.co の解決済み URL)
    for u in (post.get("urls_in_text") or []):
        if u and not re.search(r"(twitter\.com|x\.com)", u):
            if u not in seen_urls:
                seen_urls.add(u)
                add(f"外部リンク: {u}")
    # 本文 fallback
    for raw in re.findall(r"https?://[^\s)』」]+", text):
        u = raw.rstrip(".,;:)）」』]")
        if u in seen_urls:
            continue
        seen_urls.add(u)
        if re.search(r"(twitter\.com|x\.com)", u):
            continue
        if "t.co/" in u:
            add(f"本文中の短縮 URL: {u} (X 経由・要訪問で実体確認)")
        else:
            add(f"外部リンク: {u}")

    # 3. 他ユーザーへの言及(@mentions、自分自身は除く)
    self_handle = (post.get("author") or "").lstrip("@").lower()
    for m in re.findall(r"@([A-Za-z0-9_]{1,15})", text):
        if m.lower() == self_handle:
            continue
        # 上で返信先/RT元 として既に出てる @ は重複させない
        already = any(f"@{m}" in s for s in sources)
        if not already:
            add(f"@{m} への言及")

    # 3b. 引用符で囲まれた長めの発言("..." や 「...」、20 文字以上)
    for q in re.findall(r"「([^」]{20,})」", text) + re.findall(r"『([^』]{20,})』", text):
        snippet = q[:30] + ("…" if len(q) > 30 else "")
        add(f"本文に引用らしき発言: 「{snippet}」 (出典本文要確認)")
        break  # 1 件で十分

    # 4. 報道メディアの参照
    media_pat = (r"(Bloomberg|TechCrunch|Reuters|ロイター|日経|日本経済新聞|"
                 r"ITmedia|CNET|WIRED|MIT Tech Review|The Verge|Forbes)")
    for m in re.findall(media_pat, text):
        add(f"報道メディア: {m}")

    # 5. 企業/プロダクト発表(同じ「文」内で 企業名 〜 動詞 が出るパターン)
    company_pat = (r"(Anthropic|OpenAI|Google|Microsoft|Meta|Apple|Amazon|NVIDIA|xAI|"
                   r"Mistral|Cohere|GitHub|Cursor|Genspark|HeyGen|DeNA|Langgenius|"
                   r"Apify|n8n|Dify|LangChain|Replicate|Hugging\s*Face|Gemini|"
                   r"Claude|GPT|Sonnet|Opus|Haiku)")
    verb_pat = (r"(発表|リリース|公開|提供開始|ローンチ|発売|アップデート|新モデル|"
                r"オープン|登場|出した|出ました|配布|搭載|対応|搭載されます?|開発した)")
    # 企業名 〜 (40文字以内・句点跨がず) 〜 動詞
    for sent in re.split(r"[。\n]", text):
        company_match = re.search(company_pat, sent)
        verb_match = re.search(verb_pat, sent)
        if company_match and verb_match and verb_match.start() > company_match.start() \
                and verb_match.start() - company_match.end() <= 50:
            add(f"企業/ツール発表: {company_match.group(1)}")
            break  # 1 文に複数あっても 1 件で十分

    # 6. 第三者の情報・リサーチを参照
    if re.search(r"(arxiv|論文|preprint|research paper|調査レポート|白書)", text, re.IGNORECASE):
        add("論文/学術調査")
    if re.search(r"(資料|スライド|レポート|記事)\s*(が|を)?\s*(公開|発表|出ました|有益|参考|読んだ|読み)", text):
        add("第三者の資料/記事の紹介")
    # 「○○ さん」「○○氏」などの第三者言及
    if re.search(r"[A-Za-zぁ-んァ-ヶー一-龯]{2,12}\s*(さん|氏)が", text):
        add("第三者の発信を引用")

    # 7. イベント・カンファレンスでの発表
    if re.search(r"(基調講演|キーノート|keynote|登壇|発表されました|カンファレンスで)", text):
        add("イベント/カンファレンスでの情報")

    # 8. 出典明示の表現("〜によると", "〜が言ってた", "via")
    if re.search(r"(によると|によれば|via\s*@?\w|曰く|から引用)", text):
        add("出典明示あり(本文要確認)")

    if not sources:
        return "独自ツイート(明示的な外部参照なし。本人の体験/観察/意見ベースの可能性高い)"
    return " / ".join(sources)


def synthesize_why(features: list[str], score: int, eng_rate: float | None) -> str:
    """検出した特徴を元に、人気の理由を 1〜2 文で言語化。"""
    if not features:
        return "明確な構造的特徴は検出できず。アカウント自体の権威性や文脈で読まれている可能性が高い。"

    parts = []
    if any(f.startswith("数字フック") for f in features):
        parts.append("具体的な数字で得るものが冒頭で明確になっている")
    if "箇条書き構成" in features:
        parts.append("箇条書きで構造化されており読み手の認知負荷が低い")
    if "結論先出し" in features:
        parts.append("結論が先に提示されていて 3 秒で価値判断できる")
    if "短文インパクト" in features:
        parts.append("短文ゆえに引用 RT/スクショ拡散されやすい")
    if "ノウハウ網羅型(長文)" in features:
        parts.append("長文で情報密度が高く保存価値が出ている")
    if "ビジュアル訴求(画像/動画あり)" in features:
        parts.append("画像/動画でタイムライン上の停止率が上がる")
    if "逆張り/問題提起" in features:
        parts.append("常識への問題提起で引用 RT による議論を誘発")
    if "体験談/一次情報" in features:
        parts.append("一次情報の体験談で信頼性と再現可能性が示されている")
    if "公開/暴露フック" in features:
        parts.append("「公開」「暴露」系のフックで保存・後で読みたい欲を刺激")
    if "告知/イベント" in features:
        parts.append("時間/募集の限定性が行動を促している")

    summary = "、".join(parts) + "。"
    if eng_rate is not None and eng_rate >= 1.0:
        summary += f" インプレッションに対するエンゲージ率 {eng_rate}% と高水準。"
    return summary


# ─────────────────────────────────────────
# マークダウン生成
# ─────────────────────────────────────────
def render_markdown(by_account: dict, overall_top: list[dict],
                    scoring_note: str, today_jst: str, mode_note: str = "") -> str:
    L: list[str] = []
    L.append(f"# X 投稿分析レポート ({today_jst})")
    L.append("")
    L.append(f"- 対象: {len(by_account)} アカウント")
    L.append(f"- スコアリング: `{scoring_note}`")
    if mode_note:
        L.append(f"- 注意: {mode_note}")
    L.append("")

    # ── 横断 Top ──
    L.append("## 横断 Top")
    L.append("")
    L.append("| # | アカウント | スコア | フォーマット | 元投稿 |")
    L.append("|---|---|---|---|---|")
    for i, p in enumerate(overall_top, 1):
        snippet = (p.get("text") or "").replace("\n", " ").replace("|", "｜")
        if len(snippet) > 40:
            snippet = snippet[:40] + "…"
        L.append(f"| {i} | @{p['handle']} | {p['score']:,} | {p['format']} | [元ツイート]({p.get('url','')}) — {snippet} |")
    L.append("")

    # ── アカウント別 ──
    L.append("## アカウント別")
    L.append("")
    for handle, account in by_account.items():
        L.append(f"### @{handle}")
        L.append("")
        if account.get("post_count", 0) == 0:
            L.append("(取得投稿なし)")
            L.append("")
            continue

        L.append(f"- 取得投稿数: {account['post_count']}")
        if account.get("post_per_day") is not None:
            L.append(f"- 平均投稿数/日: {account['post_per_day']}")
        if account.get("format_breakdown"):
            fmt = " / ".join(f"{k}={v}" for k, v in account["format_breakdown"].items())
            L.append(f"- フォーマット内訳: {fmt}")
        # 時間帯ピーク(JST 上位 3)
        if account.get("hour_histogram_jst"):
            hh = sorted(account["hour_histogram_jst"].items(),
                        key=lambda kv: -kv[1])[:3]
            peak = " / ".join(f"{h}時({c})" for h, c in hh if c > 0)
            if peak:
                L.append(f"- 投稿ピーク時間帯(JST): {peak}")
        L.append("")

        if not account.get("top_posts"):
            L.append("(人気投稿なし)")
            L.append("")
            continue

        L.append(f"#### 人気投稿 Top {len(account['top_posts'])}")
        L.append("")
        for i, p in enumerate(account["top_posts"], 1):
            features = detect_features(p["text"], p["format"], p["length"])
            why = synthesize_why(features, p["score"], p.get("engagement_rate_pct"))
            info_source = detect_info_source(p)

            L.append(f"##### {i}. スコア {p['score']:,}")
            L.append("")
            # 本文を引用ブロックで(改行は > を付け直す)
            for line in (p.get("text") or "").split("\n"):
                L.append(f"> {line}" if line else ">")
            L.append("")
            L.append(f"- **どういう投稿か**: {p['format']} / {p['length']}文字 / "
                     f"特徴: {' / '.join(features) if features else '特に検出なし'}")
            L.append(f"- **なんで人気なのか**: {why}")
            L.append(f"- **情報ソース**: {info_source}")
            L.append(f"- **数値**: いいね {p['likes']:,} / RT {p['retweets']:,} / 返信 {p['replies']:,} / "
                     f"引用 {p['quotes']:,} / 表示 {p['views']:,} / 保存 {p['bookmarks']:,}"
                     + (f" / engage率 {p['engagement_rate_pct']}%" if p.get("engagement_rate_pct") is not None else ""))
            L.append(f"- **投稿日時**: {p['time']}")
            L.append(f"- **元ツイート**: {p.get('url') or '(URL なし)'}")
            L.append("")

    return "\n".join(L)


def main():
    import os
    ap = argparse.ArgumentParser(description="X 投稿の集計分析")
    ap.add_argument("--top", type=int, default=10,
                    help="各アカウントで抽出する Top N (default: 10)")
    ap.add_argument("--input", type=str, default=None,
                    help="入力JSONファイル。未指定なら --from-dir → stdin の順に解決。")
    ap.add_argument("--from-dir", type=str, default=None,
                    help="raw 出力のベースディレクトリ。指定すると <from-dir>/<今日(JST)>/raw_posts.json を読む。")
    ap.add_argument("--out-dir", type=str, default=None,
                    help="出力先のベースディレクトリ。指定すると <out-dir>/<今日(JST)>/analysis.md に書き出す。"
                         "未指定なら標準出力。")
    ap.add_argument("--format", choices=["md", "json"], default="md",
                    help="出力フォーマット。デフォルト md(マークダウンレポート)。"
                         "json は構造化データ(デバッグ用)。")
    args = ap.parse_args()

    # 入力解決: --input > --from-dir > stdin
    today = _today_jst_str()
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            raw = json.load(f)
        print(f"[input: {args.input}]", file=sys.stderr)
    elif args.from_dir:
        in_path = os.path.join(args.from_dir, today, "raw_posts.json")
        if not os.path.exists(in_path):
            sys.exit(f"raw_posts.json not found at: {in_path}\n"
                     f"先に fetch_x_posts.py を --out-dir {args.from_dir} で実行してください。")
        with open(in_path, encoding="utf-8") as f:
            raw = json.load(f)
        print(f"[input: {in_path}]", file=sys.stderr)
    else:
        raw = json.load(sys.stdin)
        print(f"[input: stdin]", file=sys.stderr)

    # 入力は dict (handle -> [posts]) を想定。フラットな配列が来たら "_all" に集約。
    if isinstance(raw, list):
        raw = {"_all": raw}
    if not isinstance(raw, dict):
        sys.exit("Input must be a dict {handle: [posts]} or a list of posts.")

    per_account = {h: analyze_account(h, posts, args.top) for h, posts in raw.items()}
    overall_top = cross_account_top(raw, args.top)
    scoring_note = "score = likes*1 + retweets*3 + quotes*4 + replies*2 + bookmarks*2"

    # 全投稿のエンゲージメントが 0 → WebSearch / no-engagement モードと判定して警告を自動付与
    all_zero = all(
        all(p.get("likes", 0) == 0 and p.get("views", 0) == 0 and p.get("retweets", 0) == 0
            for p in posts)
        for posts in raw.values() if posts
    )
    mode_note = ""
    if all_zero:
        mode_note = (
            "**エンゲージメント数値がすべて 0** — 取得元はおそらく Web 検索ベースで、"
            "いいね/RT/views 等の数値が手に入っていない。"
            "→ スコアによるランキングは無効、表示順は raw データの順序のまま。"
            "「どういう投稿か / なんで人気なのか / 情報ソース」の構造分析のみ信用すること。"
        )

    if args.format == "json":
        payload = json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "accounts": per_account,
            "cross_account_top": overall_top,
            "scoring_note": scoring_note,
        }, ensure_ascii=False, indent=2)
        out_filename = "analysis.json"
    else:
        payload = render_markdown(per_account, overall_top, scoring_note, today, mode_note)
        out_filename = "analysis.md"

    if args.out_dir:
        out_dir = os.path.join(args.out_dir, today)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"[wrote] {out_path}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
