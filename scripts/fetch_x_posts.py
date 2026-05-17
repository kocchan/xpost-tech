#!/usr/bin/env python3
"""
X(旧 Twitter) の指定アカウントから投稿を取得する。

認証は ~/.config/twscrape/accounts.db に保存された Cookie(auth_token + ct0)を借りて、
X の GraphQL を直接叩く方式(x_direct)。事前に setup_twscrape_cookies.py で Cookie 登録が必要。

使用例:
    # config から全件
    python fetch_x_posts.py --config config/accounts.json --out-dir output/raw

    # 単発(特定アカウントを直接)
    python fetch_x_posts.py SuguruKun_ai --limit 30 --out-dir output/raw

    # 古い投稿を切りたい
    python fetch_x_posts.py --config config/accounts.json --since-days 30 --out-dir output/raw

出力:
    --out-dir 指定時: <out-dir>/<JST 日付>/raw_posts.json (既存と自動マージ)
    未指定時:         標準出力

注意:
    - X 側の rate limit を避けるため、アカウント間で 15 秒 sleep する
    - 月 1〜2 回の運用を強く推奨。毎日大量に叩くとアカウントロックの可能性あり
    - Cookie が失効したら setup_twscrape_cookies.py で再登録
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from _env import load_dotenv

load_dotenv()  # ローカル実行時に .env を読む。GitHub Actions では Secrets が来るので no-op

# Cookie が保管された SQLite。setup_twscrape_cookies.py が書き込む。
TWSCRAPE_DB = os.environ.get(
    "TWSCRAPE_DB", os.path.expanduser("~/.config/twscrape/accounts.db")
)


# ─────────────────────────────────────────
# X GraphQL クライアント(twscrape DB の cookies を借用)
# - クエリ ID は X が時々ローテーションするので壊れたら更新する
# ─────────────────────────────────────────
_X_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_X_OPS = {
    "user": "1VOOyvKkiI3FMmkeDNxM9A/UserByScreenName",
    "tweets": "HeWHY26ItCfUmm1e6ITjeA/UserTweets",
}
_X_FEATURES_USER = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}
_X_FEATURES_TWEETS = {
    **{
        k: True
        for k in [
            "rweb_tipjar_consumption_enabled",
            "responsive_web_graphql_exclude_directive_enabled",
            "creator_subscriptions_tweet_preview_api_enabled",
            "responsive_web_graphql_timeline_navigation_enabled",
            "communities_web_enable_tweet_community_results_fetch",
            "c9s_tweet_anatomy_moderator_badge_enabled",
            "articles_preview_enabled",
            "responsive_web_edit_tweet_api_enabled",
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled",
            "view_counts_everywhere_api_enabled",
            "longform_notetweets_consumption_enabled",
            "responsive_web_twitter_article_tweet_consumption_enabled",
            "freedom_of_speech_not_reach_fetch_enabled",
            "standardized_nudges_misinfo",
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled",
            "rweb_video_timestamps_enabled",
            "longform_notetweets_rich_text_read_enabled",
            "longform_notetweets_inline_media_enabled",
            "rweb_video_screen_enabled",
            "premium_content_api_read_enabled",
            "responsive_web_grok_analyze_post_followups_enabled",
            "responsive_web_grok_analysis_button_from_backend",
            "responsive_web_grok_image_annotation_enabled",
            "responsive_web_grok_share_attachment_enabled",
            "profile_label_improvements_pcf_label_in_post_enabled",
            "responsive_web_grok_show_grok_translated_post",
        ]
    },
    "verified_phone_label_enabled": False,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
}


def _load_cookies() -> dict:
    """X_AUTH_TOKEN + X_CT0 環境変数を優先。なければ twscrape DB の最初のアクティブアカウントから。"""
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    if auth_token and ct0:
        print("[auth] 環境変数 (X_AUTH_TOKEN + X_CT0) で認証", file=sys.stderr)
        return {"auth_token": auth_token, "ct0": ct0}

    import sqlite3

    if not os.path.exists(TWSCRAPE_DB):
        sys.exit(
            f"認証情報が見つかりません: 環境変数 (X_AUTH_TOKEN / X_CT0) も "
            f"{TWSCRAPE_DB} もありません。setup_twscrape_cookies.py で登録してください。"
        )
    con = sqlite3.connect(TWSCRAPE_DB)
    row = con.execute(
        "SELECT username, cookies FROM accounts WHERE active=1 "
        "ORDER BY last_used DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        sys.exit("twscrape DB にアクティブなアカウントがありません。")
    username, cookies_json = row
    cookies = json.loads(cookies_json) if cookies_json else {}
    if "auth_token" not in cookies or "ct0" not in cookies:
        sys.exit(f"@{username} の cookies に auth_token / ct0 が揃っていません。再登録してください。")
    print(f"[auth] @{username} の Cookie で認証", file=sys.stderr)
    return cookies


def _build_headers(ct0: str) -> dict:
    return {
        "Authorization": _X_BEARER,
        "x-csrf-token": ct0,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "Accept": "*/*",
    }


def fetch_posts(
    handles: list[str],
    limit: int,
    since_days: int | None,
    target_date_jst: str | None = None,
) -> dict[str, list[dict]]:
    """target_date_jst (YYYY-MM-DD) が指定されると、JST その日の 0:00-23:59 のみ採取。
    since_days と target_date_jst が両方指定された場合は target_date_jst を優先。"""
    try:
        import httpx
    except ImportError:
        sys.exit("httpx 未インストール。 pip install httpx")

    cookies = _load_cookies()
    headers = _build_headers(cookies["ct0"])

    cutoff: datetime | None = None
    upper: datetime | None = None
    if target_date_jst:
        jst = timezone(timedelta(hours=9))
        try:
            base = datetime.strptime(target_date_jst, "%Y-%m-%d").replace(tzinfo=jst)
        except ValueError:
            sys.exit(f"--target-date は YYYY-MM-DD 形式で: {target_date_jst}")
        cutoff = base.astimezone(timezone.utc)
        upper = (base + timedelta(days=1)).astimezone(timezone.utc)
        print(f"[filter] JST {target_date_jst} のみ ({cutoff.isoformat()} 〜 {upper.isoformat()})",
              file=sys.stderr)
    elif since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    results: dict[str, list[dict]] = {}
    with httpx.Client(cookies=cookies, headers=headers, timeout=20) as client:
        for handle in handles:
            print(f"  [@{handle}] fetching...", file=sys.stderr)

            # 1. UserByScreenName
            try:
                params = {
                    "variables": json.dumps({"screen_name": handle, "withSafetyModeUserFields": True}),
                    "features": json.dumps(_X_FEATURES_USER),
                }
                r = client.get(f"https://x.com/i/api/graphql/{_X_OPS['user']}", params=params)
                r.raise_for_status()
                user_node = r.json().get("data", {}).get("user", {}).get("result")
                if not user_node or "rest_id" not in user_node:
                    print(f"    user @{handle} not found / no rest_id", file=sys.stderr)
                    results[handle] = []
                    continue
                user_id = user_node["rest_id"]
            except Exception as e:
                print(f"    UserByScreenName 失敗: {type(e).__name__}: {e}", file=sys.stderr)
                results[handle] = []
                continue

            # 2. UserTweets(ピン重複・rate limit を考慮してページング)
            tweets: list[dict] = []
            seen_ids: set[str] = set()
            cursor = None
            consecutive_empty_pages = 0
            max_pages = max(8, limit // 5)
            page_count = 0
            while len(tweets) < limit and page_count < max_pages:
                page_count += 1
                try:
                    v = {
                        "userId": user_id,
                        "count": min(40, limit),
                        "includePromotedContent": True,
                        "withQuickPromoteEligibilityTweetFields": True,
                        "withVoice": True,
                        "withV2Timeline": True,
                    }
                    if cursor:
                        v["cursor"] = cursor
                    params = {"variables": json.dumps(v), "features": json.dumps(_X_FEATURES_TWEETS)}
                    r = client.get(f"https://x.com/i/api/graphql/{_X_OPS['tweets']}", params=params)
                    r.raise_for_status()
                    page_tweets, next_cursor = _parse_user_tweets(r.json(), handle)
                except Exception as e:
                    print(f"    UserTweets 失敗: {type(e).__name__}: {e}", file=sys.stderr)
                    break

                stop = False
                added = 0
                for t in page_tweets:
                    if t["id"] in seen_ids:
                        continue
                    seen_ids.add(t["id"])
                    if t.get("time"):
                        try:
                            t_dt = datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
                            if upper and t_dt >= upper:
                                # target date より新しい投稿はスキップ(まだ範囲外、続けて古い方を探す)
                                continue
                            if cutoff and t_dt < cutoff:
                                stop = True
                                break
                        except ValueError:
                            pass
                    tweets.append(t)
                    added += 1
                    if len(tweets) >= limit:
                        stop = True
                        break

                consecutive_empty_pages = consecutive_empty_pages + 1 if added == 0 else 0
                if stop or not next_cursor or consecutive_empty_pages >= 3:
                    break

            results[handle] = tweets[:limit]
            print(f"    → {len(results[handle])} tweets", file=sys.stderr)
            time.sleep(15)  # アカウント間で休憩(rate limit 回避)

    return results


def _parse_user_tweets(payload: dict, fallback_handle: str) -> tuple[list[dict], str | None]:
    """UserTweets レスポンスから投稿リストと次ページの cursor を抽出。"""
    out: list[dict] = []
    next_cursor: str | None = None

    instrs = (
        payload.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    for instr in instrs:
        itype = instr.get("type")
        if itype == "TimelineAddEntries":
            for entry in instr.get("entries", []):
                eid = entry.get("entryId", "")
                content = entry.get("content", {})
                if content.get("entryType") == "TimelineTimelineItem":
                    item = content.get("itemContent", {})
                    if item.get("itemType") == "TimelineTweet":
                        tw = _normalize_x_tweet(
                            item.get("tweet_results", {}).get("result", {}), fallback_handle
                        )
                        if tw:
                            out.append(tw)
                if eid.startswith("cursor-bottom-"):
                    next_cursor = content.get("value")
        elif itype == "TimelinePinEntry":
            ent = instr.get("entry", {})
            content = ent.get("content", {})
            item = content.get("itemContent", {})
            if item.get("itemType") == "TimelineTweet":
                tw = _normalize_x_tweet(
                    item.get("tweet_results", {}).get("result", {}), fallback_handle
                )
                if tw:
                    out.append(tw)
    return out, next_cursor


def _normalize_x_tweet(node: dict, fallback_handle: str) -> dict | None:
    """X の Tweet ノードを共通フォーマットに変換。"""
    if not node or node.get("__typename") not in ("Tweet", "TweetWithVisibilityResults"):
        if node.get("__typename") == "TweetWithVisibilityResults":
            node = node.get("tweet", {})
        else:
            return None

    legacy = node.get("legacy", {})
    if not legacy:
        return None

    user = node.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
    username = user.get("screen_name") or fallback_handle

    # 本文(長文ツイートは note_tweet を優先)
    note = node.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
    text = (note.get("text") if note else None) or legacy.get("full_text", "")

    # メディア
    media_urls = [
        m.get("media_url_https") or m.get("expanded_url")
        for m in (legacy.get("entities", {}).get("media") or [])
        if m.get("media_url_https") or m.get("expanded_url")
    ]

    # 時刻("Tue Apr 03 12:34:56 +0000 2026" 形式 → ISO)
    iso_time = None
    if legacy.get("created_at"):
        try:
            iso_time = datetime.strptime(legacy["created_at"], "%a %b %d %H:%M:%S %z %Y").isoformat()
        except ValueError:
            iso_time = legacy["created_at"]

    tweet_id = node.get("rest_id") or legacy.get("id_str")
    views_obj = node.get("views") or {}

    # 引用元 / 返信先 / RT 元の参照情報
    quoted_id = legacy.get("quoted_status_id_str")
    quoted_url = None
    if quoted_id:
        permalink = legacy.get("quoted_status_permalink") or {}
        quoted_url = permalink.get("expanded") or f"https://x.com/i/web/status/{quoted_id}"

    in_reply_screen = legacy.get("in_reply_to_screen_name")
    in_reply_id = legacy.get("in_reply_to_status_id_str")
    in_reply_url = (
        f"https://x.com/{in_reply_screen}/status/{in_reply_id}"
        if in_reply_screen and in_reply_id
        else None
    )

    rt_node = (
        legacy.get("retweeted_status_result", {}).get("result", {})
        if legacy.get("retweeted_status_result")
        else {}
    )
    rt_legacy = rt_node.get("legacy", {}) if rt_node else {}
    rt_user = (
        rt_node.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
        if rt_node
        else {}
    )
    rt_screen = rt_user.get("screen_name")
    rt_id = rt_legacy.get("id_str") if rt_legacy else (rt_node.get("rest_id") if rt_node else None)
    rt_url = f"https://x.com/{rt_screen}/status/{rt_id}" if rt_screen and rt_id else None

    return {
        "id": str(tweet_id or ""),
        "text": text,
        "time": iso_time,
        "author": f"@{username}",
        "url": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else None,
        "likes": int(legacy.get("favorite_count", 0) or 0),
        "retweets": int(legacy.get("retweet_count", 0) or 0),
        "replies": int(legacy.get("reply_count", 0) or 0),
        "quotes": int(legacy.get("quote_count", 0) or 0),
        "views": int(views_obj.get("count", 0) or 0),
        "bookmarks": int(legacy.get("bookmark_count", 0) or 0),
        "media_urls": media_urls,
        "is_reply": bool(in_reply_id),
        "is_retweet": bool(rt_node),
        "is_quote": bool(quoted_id),
        "quoted_url": quoted_url,
        "in_reply_to_url": in_reply_url,
        "in_reply_to_screen": in_reply_screen,
        "retweeted_url": rt_url,
        "retweeted_screen": rt_screen,
        "urls_in_text": [
            u.get("expanded_url")
            for u in (legacy.get("entities", {}).get("urls") or [])
            if u.get("expanded_url")
        ],
    }


# ─────────────────────────────────────────
# config 読み込み
# ─────────────────────────────────────────
def load_config(path: str) -> dict:
    """accounts.json を読む。スキーマ:
    {
      "defaults": {"limit": 100, "since_days": 30},
      "accounts": [{"handle": "...", "category": "...", "note": "..."}]
    }
    """
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.exit(f"config file not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"config file is not valid JSON: {e}")
    if not (cfg.get("accounts") or []):
        sys.exit(f"config has no 'accounts' entries: {path}")
    return cfg


# ─────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="X の指定アカウントから投稿を取得する(x_direct)")
    ap.add_argument("handles", nargs="*",
                    help="X のハンドル(@抜き)。--config 使用時は省略可。")
    ap.add_argument("--config", type=str, default=None,
                    help="アカウントリストの JSON 設定ファイル(--config config/accounts.json)")
    ap.add_argument("--limit", type=int, default=None,
                    help="各アカウントの取得件数上限 (config の defaults.limit を上書き / なければ 100)")
    ap.add_argument("--since-days", type=int, default=None,
                    help="過去 N 日以内に絞る (省略時は期間フィルタなし=直近 limit 件)")
    ap.add_argument("--target-date", type=str, default=None,
                    help="JST の特定日 (YYYY-MM-DD) の 0:00-23:59 のみ採取 (--since-days を上書き)。"
                         " 'yesterday' で JST 昨日を指定可。")
    ap.add_argument("--out-dir", type=str, default=None,
                    help="<out-dir>/<JST 日付>/raw_posts.json に書き出す。"
                         "--target-date 指定時はその日のフォルダ、未指定なら今日のフォルダ。")
    args = ap.parse_args()

    target_date = args.target_date
    if target_date == "yesterday":
        jst = timezone(timedelta(hours=9))
        target_date = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[target-date] yesterday → {target_date} (JST)", file=sys.stderr)

    # config と CLI のマージ
    handles: list[str] = []
    limit = args.limit
    since_days = args.since_days

    if args.config:
        cfg = load_config(args.config)
        defaults = cfg.get("defaults") or {}
        if limit is None:
            limit = defaults.get("limit")
        if since_days is None:
            since_days = defaults.get("since_days")
        if args.handles:
            handles = [h.lstrip("@") for h in args.handles]
        else:
            handles = [a["handle"].lstrip("@") for a in cfg["accounts"] if a.get("handle")]
        print(f"[config: {args.config} / {len(handles)} accounts]", file=sys.stderr)
    else:
        if not args.handles:
            sys.exit("Usage: ハンドルを直接渡すか、--config <file> を指定してください。")
        handles = [h.lstrip("@") for h in args.handles]

    if limit is None:
        limit = 100
    if target_date:
        since_label = f"target-date={target_date} JST"
    else:
        since_label = f"{since_days}d" if since_days is not None else "all (latest only)"
    print(f"[mode: x_direct / limit={limit}/acct / since={since_label}]", file=sys.stderr)

    results = fetch_posts(handles, limit, since_days, target_date_jst=target_date)
    payload = json.dumps(results, ensure_ascii=False, indent=2)

    if args.out_dir:
        # 出力先フォルダ: --target-date 指定時はその日、なければ今日
        jst = timezone(timedelta(hours=9))
        date_str = target_date or datetime.now(jst).strftime("%Y-%m-%d")
        out_dir = os.path.join(args.out_dir, date_str)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "raw_posts.json")

        # 既存ファイルがあれば、新規取得分をマージ:
        # - 取得 0 件 → 既存温存
        # - 既存 >= 5 かつ新取得が既存の 50% 未満 → 既存温存(リトライ失敗系)
        merged = results
        if os.path.exists(out_path):
            try:
                with open(out_path, encoding="utf-8") as f:
                    existing = json.load(f)
                merged = dict(existing)
                replaced, skipped = [], []
                for h, posts in results.items():
                    existing_count = len(existing.get(h, []))
                    if not posts:
                        continue
                    if existing_count >= 5 and len(posts) < existing_count * 0.5:
                        skipped.append(f"{h} (new={len(posts)} < existing={existing_count})")
                        continue
                    merged[h] = posts
                    replaced.append(f"{h} ({existing_count}→{len(posts)})")
                kept = [
                    h for h in existing
                    if h not in [r.split()[0] for r in replaced]
                    and h not in [s.split()[0] for s in skipped]
                ]
                print(f"[merge] replaced={replaced}, skipped={skipped}, kept={kept}", file=sys.stderr)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[merge] 既存ファイル読み込み失敗、上書きします: {e}", file=sys.stderr)

        payload_out = json.dumps(merged, ensure_ascii=False, indent=2)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload_out)
        print(f"[wrote] {out_path}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
