# xpost-tech プロジェクト概要

**作成日**: 2026-05-17(ピボット時)
**最終更新**: 2026-05-17(GitHub Actions 一本化に変更、Claude routine は不採用)
**リポジトリ**: <https://github.com/kocchan/xpost-tech>

## ゴール

AI 副業ジャンルで X (旧 Twitter) アカウントを運用し、競合のバズ投稿を素材にしたリライト投稿で伸ばす。
**1 日 10 投稿ぶんのリライト案** を毎朝 Slack に自動配信し、人間はそこから選んで X に貼るだけにする。

## 全体フロー (3 ステップ)

```
JST 08:00 ─ GitHub Actions 01-collect.yml ─ X から「JST 昨日」の投稿を収集
                                             ↓ output/raw/<yesterday>/raw_posts.json (commit)

JST 08:30 ─ GitHub Actions 02-rewrite.yml ─ Sonnet 4.6 で Top 10 をリライト
                                             ↓ output/rewrites/<yesterday>/posts.md (commit)

JST 09:00 ─ GitHub Actions 03-slack.yml ── posts.md + raw_posts.json を Slack に投稿
                                             ↓
                                          Slack(リライト 10 通 + 画像インライン表示)
                                             ↓
                                          人間が手動で X に投稿
```

## なぜ 3 ステップ分割か

- **収集** と **リライト** と **通知** を分けると、どの段階で失敗したかが GitHub Actions のログから一目で分かる
- 中間生成物(`raw_posts.json` / `posts.md`)が git に残るので、後から見直し・やり直しが楽
- 各ステップを GitHub UI から個別に手動キックできるので、デバッグも簡単

## コスト

| 項目 | コスト |
|---|---|
| GitHub Actions(public repo) | 無料 |
| GitHub Actions(private repo) | 月 2000 分まで無料(本パイプラインは 1 日 5 分未満) |
| Claude API (Sonnet 4.6, 10 件/日) | 月 10 円未満 |
| Slack webhook | 無料 |

→ 追加月額は Claude API の数円〜数十円のみ。サブスクや固定費は一切不要。

## ファイル構成

```
xpost-tech/
├── .github/workflows/
│   ├── 01-collect.yml          ← JST 8:00 収集
│   ├── 02-rewrite.yml          ← JST 8:30 Sonnet 4.6 リライト
│   └── 03-slack.yml            ← JST 9:00 Slack 通知
├── scripts/                    ← 実行スクリプト本体 (GitHub Actions / ローカル両方から叩く)
│   ├── fetch_x_posts.py        ← Cookie 環境変数 + --target-date 対応
│   ├── analyze_posts.py        ← (任意) ランキング md
│   ├── setup_twscrape_cookies.py
│   ├── rewrite_top.py          ← Top N をリライト → posts.md
│   └── notify_slack.py         ← posts.md + raw_posts.json → Slack
├── .claude/skills/             ← Skill メタデータ (使い方ドキュメント) のみ
│   ├── x-collecter/SKILL.md
│   └── x-slack-poster/SKILL.md
├── config/
│   └── accounts.json           ← 11 アカウント(core 6 + side_hustle 5)
├── doc/
│   └── project_overview.md     ← 本ファイル
└── SETUP.md                    ← 初期セットアップ手順(必読)
```

## 必要な Secrets(GitHub)

| 名前 | 用途 |
|---|---|
| `X_AUTH_TOKEN` | X (Twitter) 認証 Cookie |
| `X_CT0` | X (Twitter) CSRF Cookie |
| `ANTHROPIC_API_KEY` | Claude API |
| `SLACK_WEBHOOK_URL` | Slack 通知先 |

詳細は [SETUP.md](../SETUP.md) を参照。

## 使用モデル

**Claude Sonnet 4.6** (`claude-sonnet-4-6`)
- `thinking: {"type": "disabled"}` + `output_config.effort: "low"` (短文 chat 系の推奨設定)
- 入力 $3 / 出力 $15 per 1M tokens
- 1 件あたり ~700 入力トークン + ~300 出力トークン = 約 0.7 円/月(10 件/日 × 30 日)

## 収集対象アカウント (11 件)

`config/accounts.json` 参照。

| category | アカウント |
|---|---|
| `core` / `business` / `major` | SuguruKun_ai, so_ainsight, dify_base, 0x__tom, masahirochaen, satori_sz9 |
| `side_hustle` | Naoki_GPT, ai_jitan, akaoniudetate, develogon0, kaneki_ai888 |

## メディア (画像/動画) の扱い

Slack 通知では元投稿のメディア URL を image ブロックで埋め込む(画像はインライン表示)。

- **見るだけ・参考目的** → OK
- **そのままリライト投稿に貼る** → ❌ NG(著作権侵害)
- 自作のスクショ・図解を作り直して貼るのが正解

## 運用上の注意

- **Cookie 失効**: `X_AUTH_TOKEN` / `X_CT0` は数週間〜数ヶ月で失効する。01-collect.yml が 401/403 で失敗したら GitHub Secrets を手動更新
- **rate limit**: 11 アカウント × 15 秒 sleep = 約 3 分。X の限界には余裕で収まる

## 関連 Skill / ドキュメント

- [.claude/skills/x-collecter/SKILL.md](../.claude/skills/x-collecter/SKILL.md) — 収集
- [.claude/skills/x-slack-poster/SKILL.md](../.claude/skills/x-slack-poster/SKILL.md) — リライト + 通知
- [SETUP.md](../SETUP.md) — 初期セットアップ(必読)
