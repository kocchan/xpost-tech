#!/usr/bin/env python3
"""
twscrape にブラウザから抜いた Cookie を注入する。
パスワードログインが Cloudflare ブロックされるときの回避策。

使い方:
    python scripts/setup_twscrape_cookies.py

ブラウザで X にログインした状態にしてから、DevTools の
Application → Cookies → https://x.com から auth_token と ct0 の値を
コピーして、対話入力する。
"""
import asyncio
import getpass
import os
import sys

DB_PATH = os.environ.get("TWSCRAPE_DB",
                         os.path.expanduser("~/.config/twscrape/accounts.db"))


async def main():
    try:
        from twscrape import API
    except ImportError:
        sys.exit("twscrape 未インストール。先に `pip install twscrape`")

    if not os.path.exists(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    print(f"[db] {DB_PATH}\n")
    print("ブラウザの DevTools(Application → Cookies → https://x.com)から")
    print("auth_token と ct0 の値を取り出して、ここに貼り付けてください。\n")

    username = input("X username (@ なし): ").strip().lstrip("@")
    if not username:
        sys.exit("username が空です")
    auth_token = getpass.getpass("auth_token (Cookie): ").strip()
    ct0 = getpass.getpass("ct0 (Cookie): ").strip()
    if not auth_token or not ct0:
        sys.exit("auth_token / ct0 が両方とも必要です")

    api = API(DB_PATH)

    # 既存のレコードがあれば上書きするため、一旦削除
    try:
        await api.pool.delete_accounts(username)
        print(f"[note] 既存の @{username} レコードを削除して登録し直します")
    except Exception:
        pass

    cookies_str = f"auth_token={auth_token}; ct0={ct0}"
    try:
        # add_account は cookies を文字列か辞書で受け付ける(バージョンによる)
        await api.pool.add_account(
            username=username,
            password="DUMMY_PASS_NOT_USED",
            email="dummy@example.com",
            email_password="DUMMY",
            cookies=cookies_str,
        )
        print(f"[added] @{username} cookies 注入完了")
    except TypeError:
        # 古い API: 辞書で渡す
        await api.pool.add_account(
            username=username,
            password="DUMMY",
            email="dummy@example.com",
            email_password="DUMMY",
            cookies={"auth_token": auth_token, "ct0": ct0},
        )
        print(f"[added] @{username} (legacy API)")
    except Exception as e:
        sys.exit(f"add_account 失敗: {e}")

    # アカウントをアクティブ化(Cookie ベースなので login_all 不要、しかし ensure)
    print("\n簡易テスト取得を実行します(elonmusk の最新 3 投稿)...")
    try:
        user = await api.user_by_login("elonmusk")
        if not user:
            print("[error] elonmusk lookup 失敗。Cookie が有効か確認してください")
            return
        print(f"[ok] elonmusk lookup → user_id={user.id}")

        count = 0
        async for tw in api.user_tweets(user.id, limit=3):
            count += 1
            snippet = (tw.rawContent or "")[:60].replace("\n", " ")
            print(f"  - {tw.likeCount:>6} likes / {snippet}")
        print(f"\n[success] {count} 件取れました。本番運用可能です。")
        print("\n本番取得コマンド:")
        print("  python3 scripts/fetch_x_posts.py \\")
        print("      --config config/accounts.json --out-dir output/raw")
    except Exception as e:
        print(f"[error] テスト取得失敗: {e}")
        print("→ Cookie の値が間違っているか、X 側でセッションが切れている可能性。")


if __name__ == "__main__":
    asyncio.run(main())
