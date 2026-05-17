---
name: x-slack-poster
description: x-collecter が集めたバズ投稿を Claude Sonnet 4.6 で「AI 副業」ジャンル向けに完全リライトし、Slack に通知する Skill。スクリプトは 2 本に分かれており、rewrite_top.py がリライトを posts.md に保存、notify_slack.py が posts.md と元 raw を読んで Slack に画像付きで投げる。「リライト案を Slack に流して」「バズ投稿を AI 副業向けに書き直して」と言われたら使う。X への投稿そのものは人間が手動で行う。
---

# X リライト → Slack 通知 Skill

AI 副業アカウント運用パイプラインの **中流・下流(リライト → 通知)** を担う。
上流の `x-collecter` が集めたバズ投稿を入力に、Claude API で書き直し、Slack に流すまでが責務。
X への投稿は人間が手動で行う(完全自動化は採用しない)。

## パイプライン上の位置

```
[x-collecter] (JST 8:00 / GitHub Actions)
       ↓ output/raw/<日付>/raw_posts.json
[rewrite_top.py] (JST 8:30 / Claude routine、フォールバック GitHub Actions 8:45)
       ↓ output/rewrites/<日付>/posts.md
[notify_slack.py] (JST 9:00 / GitHub Actions)
       ↓
Slack(リライト案 + 元投稿 URL + 画像インライン表示)
       ↓
人間が手動で X に投稿
```

## スクリプト構成

| スクリプト | 役割 | 実行主体 |
|---|---|---|
| `scripts/rewrite_top.py` | Top N をリライトして `output/rewrites/<日付>/posts.md` に保存 | Claude routine(本番) / GitHub Actions(フォールバック) / ローカル |
| `scripts/notify_slack.py` | `posts.md` と元 `raw_posts.json` を読んで Slack に画像付きで送信 | GitHub Actions(本番) / ローカル |

## モデルとパラメータ

- **モデル**: `claude-sonnet-4-6`(リライト品質とコストのバランス)
- **`thinking`**: `{"type": "disabled"}`(短文 chat 系で thinking 不要)
- **`output_config.effort`**: `"low"`(Sonnet 4.6 デフォルトの `high` を明示的に下げる)
- **`max_tokens`**: 1024(1 投稿あたり 1 本の本文 + 余裕)

参考: `claude-api` Skill の Sonnet 4.6 推奨「非 thinking chat ワークロードは `disabled` + `effort: low`」。

## 使い方

### 1. リライト (rewrite_top.py)

**本番(Claude routine 経由)**: 毎朝 JST 8:30 に routine が `rewrite_top.py --target-date yesterday --top 10` を実行し、posts.md を commit + push する。詳細は [SETUP.md](../../../SETUP.md) を参照。

**フォールバック GitHub Actions**: `.github/workflows/02-rewrite.yml` が JST 8:45 に走り、posts.md が無ければ生成する。

**ローカル手動**:

```bash
cd /Users/noharakouhei/Downloads/xpost-tech
source .venv/bin/activate

ANTHROPIC_API_KEY=sk-... \
python3 .claude/skills/x-slack-poster/scripts/rewrite_top.py \
    --raw-root output/raw \
    --rewrite-root output/rewrites \
    --target-date yesterday \
    --top 10
```

出力: `output/rewrites/<対象日>/posts.md` に 10 件分のリライト案 + 元投稿 URL + メディア URL。

### 2. Slack 通知 (notify_slack.py)

**本番(GitHub Actions)**: `.github/workflows/03-slack.yml` が毎朝 JST 9:00 に実行。

**ローカル手動 / Dry-run**:

```bash
# 実際に Slack に投げる
SLACK_WEBHOOK_URL=https://hooks.slack.com/... \
python3 .claude/skills/x-slack-poster/scripts/notify_slack.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday

# Slack に送らず stdout に dump
python3 .claude/skills/x-slack-poster/scripts/notify_slack.py \
    --raw-root output/raw --rewrite-root output/rewrites \
    --target-date yesterday --dry-run
```

Slack には:
- イントロ 1 通(対象日 + 件数)
- リライト案 10 通(本文 + 元投稿 URL + 画像 image ブロック)

が連続で送られる。

## リライトのルール(プロンプトに埋め込み済み)

1. 元投稿の文章を **1 文字もコピーしない**(構成と訴求の型だけ参考)
2. 280 字以内(X の本文上限)
3. ターゲット: AI 副業に興味がある会社員 / 副業初心者
4. 煽り訴求(根拠なき「月100万」「誰でも稼げる」)は禁止
5. ハッシュタグは入れない
6. 改行と空行で読みやすく
7. 1 投稿につき 1 本(=10 件の元投稿から 10 本のリライト案)

## メディア(画像/動画)の扱い

- Slack 通知に **元投稿のメディア URL** を image ブロックで埋め込み(画像は Slack でインライン表示)
- これは「人間の参考」用途。**そのままリライト投稿に貼るのは著作権侵害になるので NG**
- 自作のスクショや図解を作り直して投稿に貼るのは OK

## 環境変数

| 変数 | 必須 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | rewrite_top.py | Claude API 認証 |
| `SLACK_WEBHOOK_URL` | notify_slack.py | Slack 通知先 |

Slack Webhook は環境変数のほか、以下のファイルからも自動検出:
1. `SLACK_WEBHOOK_URL` 環境変数
2. `~/.claude/skills/x-post-drafter/slack.json` の `webhook_url`
3. プロジェクト直下 `config/slack.json` の `webhook_url`

## 関連 Skill

- [x-collecter](../x-collecter/SKILL.md) — 上流の収集 Skill
- セットアップ全体: [`SETUP.md`](../../../SETUP.md)
