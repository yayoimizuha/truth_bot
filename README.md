# Truth Bot

SNS（Truth Social）向けのAIエージェントです。通知を監視し、メンションやリプライに対して自動で応答（テキスト、画像、動画）を行います。

## 特徴

- **自動応答専用**: 自発的な投稿やフォローは行わず、通知（メンション・リプライ）への応答に特化しています。
- **マルチモーダル対応**: 通常の会話だけでなく、コマンドによる直接的な画像・動画生成が可能です。
- **正規化処理**: SNS特有のHTMLタグ、メンション、短縮URLなどを適切に処理してAIに渡します。
- **会話履歴の考慮**: 対象の投稿から親投稿を辿り、文脈に沿った応答を生成します。
- **コマンドシステム**: 行頭コマンド（`/image`, `/video`）により、詳細なパラメータ指定でのメディア生成が可能です。

## システム構成

- **Agent Service (`timeline_agent.py`)**: 全体のエントリポイント。通知のポーリングと処理ループを管理します。
- **Browser Proxy (`ts_hook_server.py`)**: Truth SocialのCloudflare保護やログインを回避するためのブラウザベースのプロキシサーバー。
- **SNS Agent (`sns_agent/`)**: コアロジックモジュール。
    - `service.py`: 処理フローのオーケストレーション。
    - `responder.py`: LLM（litellm / pydantic-ai）を使用した対話生成。
    - `commands.py`: `/image`, `/video` コマンドのパース。
    - `media.py`: 画像・動画生成バックエンドとの連携。
    - `truthsocial.py`: Truth Social APIとの通信。
    - `normalizer.py`: 入力テキストの正規化。
    - `publisher.py`: 最終的な投稿（メディア添付含む）の実行。
    - `state_store.py`: 処理済み通知の永続化管理（SQLite）。

## セットアップ

### 必要条件

- Python 3.12以上
- [uv](https://github.com/astral-sh/uv) (推奨)
- [Playwright](https://playwright.dev/) ブラウザ (scrapling/camoufox用)

### インストール

```bash
uv sync
```

### 環境設定

`.env` ファイルを作成し、必要なAPIキーや設定を記述してください。

```env
# Truth Social 設定
TRUTHSOCIAL_CLIENT_ID=your_client_id
TRUTHSOCIAL_CLIENT_SECRET=your_client_secret
TRUTHSOCIAL_USERNAME=your_username
TRUTHSOCIAL_PASSWORD=your_password

# LLM 設定 (litellm互換)
OPENROUTER_API_KEY=your_key
DEFAULT_MODEL=openrouter/anthropic/claude-3-5-sonnet

# 画像・動画生成プロキシ
TS_HOOK_SERVER_BASE_URL=http://localhost:8000

# 動作設定
NOTIFICATION_POLL_SECONDS=20
STATE_DB_PATH=history.db
```

## 使用方法

### 1. プロキシサーバーの起動

Truth SocialのAPI制限や保護を回避するため、まずプロキシサーバーを起動します。

```bash
uv run fastapi run ts_hook_server.py --port 8000
```

### 2. エージェントの起動

別のターミナルでエージェントを起動します。

```bash
uv run timeline_agent.py
```

## コマンド仕様

### 画像生成 (`/image`)

```text
/image
model: sdxl
size: 1024x1024
count: 1

宇宙で踊る猫のプロンプト
```

### 動画生成 (`/video`)

```text
/video
model: luma

夕焼けの海岸線を走る馬
```

## 開発状況

- `outdated/`: 過去の実験コードや古い実装が含まれています（動作対象外）。
- `tests/`: 基本的な機能のテストコードを順次追加中。
