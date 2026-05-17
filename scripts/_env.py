"""ローカル実行時に .env から環境変数を読み込む最小ローダー。

依存ゼロ(標準ライブラリのみ)。`KEY=VALUE` 1 行 1 件、`#` 始まりはコメント、
クォート (`"` / `'`) で囲まれた値はそのまま除去する。

既に環境変数が設定されている場合は **上書きしない** (GitHub Actions の Secrets を
ローカル .env で誤って上書きしないため)。

使い方: 各スクリプトの先頭で

    from _env import load_dotenv
    load_dotenv()
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> bool:
    """.env を読んで os.environ に注入。既存の env は温存。
    ファイルが無ければ何もせず False を返す(GitHub Actions 上では Secret から直接来る想定)。"""
    p = Path(path)
    if not p.is_file():
        # スクリプト実行ディレクトリ直下に無ければ、CWD のひとつ上 (= リポジトリルート) も試す
        alt = Path.cwd() / ".env"
        if alt.is_file():
            p = alt
        else:
            return False

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 値の前後クォートを 1 ペアだけ剥がす
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
    return True
