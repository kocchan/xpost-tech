# セットアップ手順 (xpost-tech)

毎朝 JST 8:00 → 8:30 → 9:00 の 3 ステップで X バズ投稿の収集 → リライト → Slack 通知を自動化する。
GitHub Actions と Claude routine を使うので、月額固定費は **0 円**(Claude Pro/Max サブスク + API 微課金のみ)。

リポジトリ: <https://github.com/kocchan/xpost-tech>

## 1. ローカルを GitHub に push

```bash
cd /Users/noharakouhei/Downloads/xpost-tech

# .gitignore (まだ無ければ)
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.env
EOF

git init
git branch -M main
git add .
git commit -m "initial pivot: AI 副業向け 3 ステップパイプライン"
git remote add origin https://github.com/kocchan/xpost-tech.git
git push -u origin main
```

(gh CLI が入っていれば `gh repo view kocchan/xpost-tech` で確認できる)

## 2. GitHub Secrets を登録

リポジトリの **Settings → Secrets and variables → Actions** で以下を登録:

| 名前 | 値 | 取得方法 |
|---|---|---|
| `X_AUTH_TOKEN` | X の auth_token Cookie | ブラウザで X にログイン → DevTools → Application → Cookies → `https://x.com` |
| `X_CT0` | X の ct0 Cookie | 同上 |
| `ANTHROPIC_API_KEY` | Claude API キー | <https://console.anthropic.com/> |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL | 既に `~/.claude/skills/x-post-drafter/slack.json` にあるものを流用可 |

> **Cookie 失効**: auth_token / ct0 は数週間〜数ヶ月で失効する。Workflow 01 が 401/403 になったら同じ手順で更新。

## 3. Claude routine を登録(ステップ 2 担当)

Claude Code で以下のように `/schedule` (もしくは Claude routines の UI) で登録:

- **頻度**: 毎日 JST 8:30
- **プロンプト**:

```
リポジトリ kocchan/xpost-tech を pull して、以下を実行してください:

1. python3 scripts/rewrite_top.py \
     --raw-root output/raw --rewrite-root output/rewrites \
     --target-date yesterday --top 10
2. 生成された output/rewrites/<日付>/posts.md を git add → commit → push

ANTHROPIC_API_KEY は環境に既にあるはず(なければ routine の設定で追加)。
失敗したら詳細を Slack に送って終了してください。
```

routine 経由が失敗する/動かない日は `.github/workflows/02-rewrite.yml` が 8:45 にフォールバックで走り、`posts.md` がまだ無ければ生成して push する(routine が成功した日はスキップ)。**Claude routine を使わずに完全 GitHub Actions 一本化したい場合は、routine を登録せず 02-rewrite.yml だけ有効にすればよい**。

## 4. cron スケジュールまとめ

| 時刻 (JST) | 実行主体 | やること |
|---|---|---|
| 08:00 | GitHub Actions `01-collect.yml` | X から「JST 昨日」の投稿を取得 → `output/raw/<yesterday>/raw_posts.json` を commit |
| 08:30 | Claude routine | `rewrite_top.py` で Top 10 を Sonnet 4.6 リライト → `output/rewrites/<yesterday>/posts.md` を commit |
| 08:45 | GitHub Actions `02-rewrite.yml` (保険) | routine が動かなかった日だけリライト実行 |
| 09:00 | GitHub Actions `03-slack.yml` | `posts.md` + `raw_posts.json` を読んで Slack に 10 メッセージ送信(画像はインライン展開) |

すべての workflow は **手動実行** にも対応(GitHub UI から **Run workflow**、`target_date` を任意指定可)。

## 5. 動作確認

GitHub 側を待たずに手元で動かす場合:

```bash
# venv に依存を入れる
python3 -m pip install httpx anthropic

# (1) 収集を「今日」付けで試す
X_AUTH_TOKEN=xxx X_CT0=yyy \
python3 scripts/fetch_x_posts.py \
    --config config/accounts.json \
    --target-date yesterday \
    --limit 30 \
    --out-dir output/raw

# (2) リライト
ANTHROPIC_API_KEY=sk-... \
python3 scripts/rewrite_top.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday --top 5

# (3) Slack 通知 (dry-run で stdout に出すだけ)
python3 scripts/notify_slack.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday --dry-run
```

## 6. 画像 / 動画の取り扱いについて(重要)

Slack 通知では **元投稿のメディア URL をそのままインライン展開** で表示する(画像なら Slack でプレビュー表示される)。

- **見るだけ・参考目的** → OK。投稿の構成と一緒に画像も確認できる
- **そのまま自分のリライト投稿に貼る** → ❌ NG。他人の画像を無断使用すると著作権侵害になる。スクショ等で「参考に自作し直す」のは OK

Slack に流れた画像はあくまで「人間の参考材料」と割り切る運用にしてください。

## 7. コスト見積もり

| 項目 | コスト |
|---|---|
| GitHub Actions(パブリックリポジトリ) | 無料 |
| GitHub Actions(プライベートリポジトリ) | 月 2,000 分まで無料(本パイプラインは 1 日 5 分未満) |
| Claude API (Sonnet 4.6, リライト 10 件/日) | 月およそ **10 円未満** |
| Claude routine | Claude Pro/Max サブスク内 |
| Slack incoming webhook | 無料 |

→ 既に Claude サブスクに入っているなら **追加月額は実質 0 円**。
