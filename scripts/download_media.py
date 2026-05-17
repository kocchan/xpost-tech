#!/usr/bin/env python3
"""raw_posts.json から video_urls を読み出して mp4 をローカルに保存する。

ローカル参考素材専用 (リライト案のネタ確認や、自作 AI 動画の構成参考に使う)。
他人の動画を自分の投稿に貼るのは著作権侵害なので絶対にやらないこと。

使用例:
    # 昨日 JST 分の動画をローカルに DL
    python3 scripts/download_media.py --target-date yesterday

    # 特定日
    python3 scripts/download_media.py --target-date 2026-05-17

    # 上書きしないで既存はスキップ
    python3 scripts/download_media.py --target-date 2026-05-17 --skip-existing
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone


def _filename_from_url(url: str) -> str:
    """URL の末尾から拡張子付きファイル名を抽出 (?tag= などは落とす)。"""
    base = url.split("?")[0].rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "video.mp4"


def download(url: str, dest: str, skip_existing: bool = False) -> bool:
    if skip_existing and os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  [skip] {dest}", file=sys.stderr)
        return False
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        out.write(resp.read())
    return True


def main():
    ap = argparse.ArgumentParser(description="raw_posts.json の動画をローカル保存")
    ap.add_argument("--raw-root", default="output/raw")
    ap.add_argument("--target-date", required=True, help="YYYY-MM-DD or 'yesterday' (JST)")
    ap.add_argument("--skip-existing", action="store_true", help="既にファイルがあればスキップ")
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

    media_dir = os.path.join(args.raw_root, target_date, "media")
    os.makedirs(media_dir, exist_ok=True)

    total_videos = 0
    downloaded = 0
    skipped = 0
    failed = 0
    for handle, posts in raw.items():
        for p in posts:
            vurls = p.get("video_urls") or []
            if not vurls:
                continue
            tweet_id = p.get("id") or "unknown"
            for idx, url in enumerate(vurls):
                total_videos += 1
                suffix = f"_{idx}" if len(vurls) > 1 else ""
                name = f"{handle}_{tweet_id}{suffix}_{_filename_from_url(url)}"
                dest = os.path.join(media_dir, name)
                try:
                    if download(url, dest, args.skip_existing):
                        size_kb = os.path.getsize(dest) // 1024
                        print(f"  [ok] {name} ({size_kb} KB) ← {p.get('url')}", file=sys.stderr)
                        downloaded += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"  [fail] {name}: {type(e).__name__}: {e}", file=sys.stderr)
                    failed += 1

    print(
        f"[done] target={target_date} / videos={total_videos} "
        f"(downloaded={downloaded} / skipped={skipped} / failed={failed}) → {media_dir}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
