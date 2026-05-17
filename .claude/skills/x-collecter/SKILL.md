---
name: x-collecter
description: X(旧 Twitter)の競合・参考アカウントから「AI 副業」ジャンルでバズった投稿を取得し、エンゲージメント順にランキングする Skill。後段の x-slack-poster がリライト対象を選ぶ材料として使う。「バズ投稿を集めて」「リライト候補を出して」「競合の Top 投稿を取って」と言われたら使う。出力は output/raw/<日付>/raw_posts.json と output/analysis/<日付>/analysis.md。
---

# X バズ投稿コレクター Skill

AI 副業アカウント運用パイプラインの **上流(収集)** を担う。
下流の `x-slack-poster` がリライト候補を選ぶための「素材」を集めるのが役割。

## パイプライン上の位置

```
[x-collecter]            → [x-slack-poster]            → 手動で X 投稿
  ↑ このスキル               リライト + Slack 通知
  バズ投稿を取得 + ランキング
```

## アーキテクチャ

```
config/accounts.json           ← 収集対象ハンドル(競合 + AI 副業系)
       ↓
scripts/fetch_x_posts.py       ← X GraphQL を直叩き(twscrape DB の Cookie 借用)
       ↓
output/raw/<日付>/raw_posts.json
       ↓
scripts/analyze_posts.py       ← エンゲージメントスコアでランキング + 構造特徴抽出
       ↓
output/analysis/<日付>/analysis.md   ← Top N + 出典 URL 付き
       ↓
(次は x-slack-poster がこれを読み込んでリライト)
```

## 初回セットアップ

ローカル venv に依存パッケージ:

```bash
cd /Users/noharakouhei/Downloads/xpost-tech
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip httpx twscrape
```

X の Cookie を登録(初回のみ。失効時も同じ手順):

```bash
# ブラウザで X にログイン → DevTools → Application → Cookies → https://x.com
# auth_token と ct0 の値をコピー
python3 .claude/skills/x-collecter/scripts/setup_twscrape_cookies.py
```

elonmusk の最新 3 投稿が取れれば成功。Cookie は `~/.config/twscrape/accounts.db` に保存。

## 使い方

### 1. 収集対象を `config/accounts.json` で管理

```json
{
  "defaults": { "limit": 30 },
  "accounts": [
    { "handle": "SuguruKun_ai", "category": "core", "note": "..." }
  ]
}
```

- `handle` は @ なし
- `category`: `core` / `business` / `major` / `side_hustle` 等(自由ラベル)
- `defaults.since_days` を入れると過去 N 日に絞れる(省略時は直近 limit 件)
- `--target-date YYYY-MM-DD` / `--target-date yesterday`: JST のその日 0:00-23:59 のみ採取 + 出力フォルダもその日付に

### 2. 取得 → 集計

**本番運用 (GitHub Actions)**: `.github/workflows/01-collect.yml` が毎朝 JST 8:00 に `--target-date yesterday` で自動実行。Cookie は Secrets (`X_AUTH_TOKEN` / `X_CT0`) から注入され、結果は `output/raw/<yesterday>/raw_posts.json` に commit される。

**ローカル手動実行**:

```bash
cd /Users/noharakouhei/Downloads/xpost-tech
source .venv/bin/activate

# JST 昨日の投稿のみ → output/raw/<yesterday>/raw_posts.json
python3 .claude/skills/x-collecter/scripts/fetch_x_posts.py \
    --config config/accounts.json \
    --target-date yesterday \
    --out-dir output/raw

# (任意) ランキングレポートが欲しい場合
python3 .claude/skills/x-collecter/scripts/analyze_posts.py \
    --from-dir output/raw --out-dir output/analysis --top 10
```

単発調査(特定アカウントだけ見たいとき):

```bash
python3 .claude/skills/x-collecter/scripts/fetch_x_posts.py \
    SuguruKun_ai --limit 30 --out-dir output/raw
```

### Cookie の渡し方(優先順)

1. **環境変数** `X_AUTH_TOKEN` + `X_CT0` — GitHub Actions / 一時的なローカル実行向け
2. **twscrape DB** `~/.config/twscrape/accounts.db` — `setup_twscrape_cookies.py` で登録、対話的な開発運用向け

### 3. 出力(リライト候補の素材)

`output/analysis/<日付>/analysis.md` がそのまま読める Top ランキング。
各投稿について本文・スコア・構造特徴・出典 URL が並んでいる。

x-slack-poster はこの中から「リライト対象」を 1〜数件選ぶ前提。

## 出力先(JST 日付フォルダ自動振り分け)

| パス | 内容 |
|---|---|
| `output/raw/<YYYY-MM-DD>/raw_posts.json` | 生取得データ |
| `output/analysis/<YYYY-MM-DD>/analysis.md` | Top ランキング |

同日内の再実行は自動マージ:
- 取得 0 件 → 既存温存
- 既存 ≥ 5 件 かつ 新取得 < 既存の 50% → 既存温存(リトライ失敗系)
- それ以外 → 新取得で上書き

## スコアリング

```
score = likes*1 + retweets*3 + quotes*4 + replies*2 + bookmarks*2
```

拡散(RT/引用)を重く、議論(返信)と保存価値(ブックマーク)を中重み。
views が取れていれば `engagement_rate_pct` も併記。

## 運用上の注意

- **頻度**: 月 1〜2 回 + 小ロット推奨。毎日大量に叩くと X が rate limit / Cookie 失効を起こす
- **Cookie 失効**: 数週間〜数ヶ月で失効。fetch が認証エラーを返したら `setup_twscrape_cookies.py` で再登録
- **長文の末尾切れ**: X の UserTweets エンドポイントは note tweet を完全に返さないケースがある。本文末尾が不自然に切れていたら、URL を踏んで人間が原文確認(または将来 TweetDetail で再取得を実装)
- **rate limit**: アカウント間で 15 秒 sleep。429 が出たら 10〜15 分待ってから 0 件アカウントだけ再フェッチ(マージで既存温存される)
- **規約**: X の利用規約上スクレイピングはグレーゾーン。個人の運用調査用途であれば実害は出ないが、大規模再配布は避ける

## 関連 Skill

- [x-slack-poster](../x-slack-poster/SKILL.md) — この Skill の出力を入力にして、Claude API でリライト + Slack に投稿案を通知する
