# セットアップ手順 (xpost-tech)

毎朝 JST 8:00 → 8:30 → 9:00 の 3 ステップで X バズ投稿の収集 → リライト → Slack 通知を自動化する。
**実行は全部 GitHub Actions**。Claude API も GitHub Actions の中から叩く。

リポジトリ: <https://github.com/kocchan/xpost-tech>

## 1. ローカルを GitHub に push(済んでいればスキップ)

```bash
cd /Users/noharakouhei/Downloads/xpost-tech

# .gitignore (まだ無ければ)
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.env
.claude/settings.local.json
EOF

git init -b main
git add .
git commit -m "initial pivot"
git remote add origin https://github.com/kocchan/xpost-tech.git
git push -u origin main
```

## 2. GitHub Secrets を登録

リポジトリの **Settings → Secrets and variables → Actions** で以下を登録:

| 名前 | 値 | 取得方法 |
|---|---|---|
| `X_AUTH_TOKEN` | X の auth_token Cookie | ブラウザで X にログイン → DevTools → Application → Cookies → `https://x.com` |
| `X_CT0` | X の ct0 Cookie | 同上 |
| `ANTHROPIC_API_KEY` | Claude API キー | <https://console.anthropic.com/> |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL | 既存の `~/.claude/skills/x-post-drafter/slack.json` の `webhook_url` を流用可 |

> **Cookie 失効**: auth_token / ct0 は数週間〜数ヶ月で失効する。01-collect workflow が 401/403 になったら同じ手順で更新。

gh CLI 派なら一括登録:

```bash
brew install gh && gh auth login
gh secret set X_AUTH_TOKEN      --repo kocchan/xpost-tech
gh secret set X_CT0             --repo kocchan/xpost-tech
gh secret set ANTHROPIC_API_KEY --repo kocchan/xpost-tech
gh secret set SLACK_WEBHOOK_URL --repo kocchan/xpost-tech \
    < <(jq -r .webhook_url ~/.claude/skills/x-post-drafter/slack.json)
```

## 3. cron スケジュール

| 時刻 (JST) | Workflow | やること |
|---|---|---|
| 08:00 | `01-collect.yml` | X から「JST 昨日」の投稿を取得 → `output/raw/<yesterday>/raw_posts.json` を commit |
| 08:30 | `02-rewrite.yml` | `rewrite_top.py` で Top 10 を Sonnet 4.6 リライト → `output/rewrites/<yesterday>/posts.md` を commit |
| 09:00 | `03-slack.yml` | `posts.md` + `raw_posts.json` を読んで Slack に 10 メッセージ送信(画像はインライン展開) |

すべての workflow は **手動実行** にも対応(GitHub UI から **Run workflow**、`target_date` を任意指定可)。

## 4. 動作確認

### A. GitHub 上で手動キック(推奨)

<https://github.com/kocchan/xpost-tech/actions> の Actions タブで、上から順に Run workflow:

1. **01 Collect** → 完了を待つ
2. **02 Rewrite** → 完了を待つ
3. **03 Notify Slack** → 完了を待つ
4. Slack に 10 通リライト案が届けば成功

### B. 手元で動かす場合 (.env を使う)

ローカルで叩く場合は `.env` に API キーを書いておけば各スクリプトが自動で読む。
GitHub Secrets と環境変数名は同じなので 1 ファイルで両方の運用に対応できる。

```bash
# 1) .env を用意 (初回のみ)
cp .env.example .env
# .env を編集して X_AUTH_TOKEN / X_CT0 / ANTHROPIC_API_KEY / SLACK_WEBHOOK_URL を埋める

# 2) venv に依存を入れる
python3 -m venv .venv
source .venv/bin/activate
pip install httpx anthropic

# 3) 収集
python3 scripts/fetch_x_posts.py \
    --config config/accounts.json \
    --target-date yesterday \
    --limit 30 \
    --out-dir output/raw

# 4) リライト
python3 scripts/rewrite_top.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday --top 5

# 5) Slack 通知 (dry-run で stdout に出すだけ)
python3 scripts/notify_slack.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday --dry-run
```

> 既に環境変数が export 済みの場合は `.env` の値で **上書きされない**(env が優先)。
> CI と手元で挙動が変わらないよう、`.env` は「手元のデフォルト」として扱う設計。

## 5. 画像 / 動画の取り扱いについて(重要)

Slack 通知では **元投稿のメディア URL をそのままインライン展開** で表示する(画像なら Slack でプレビュー表示)。

- **見るだけ・参考目的** → OK
- **そのままリライト投稿に貼る** → ❌ NG(著作権侵害)。スクショで自作し直して貼るのが正解

## 6. コスト見積もり

| 項目 | コスト |
|---|---|
| GitHub Actions(public repo) | 無料 |
| GitHub Actions(private repo) | 月 2,000 分まで無料(本パイプラインは 1 日 5 分未満) |
| Claude API (Sonnet 4.6, 10 件/日) | 月およそ **10 円未満** |
| Slack incoming webhook | 無料 |

→ 追加月額は Claude API の数円〜数十円のみ。サブスクや追加固定費は一切不要。
