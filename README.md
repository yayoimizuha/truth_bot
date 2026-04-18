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

`.env` ファイルを作成し、必要な設定を記述してください。

まずは最小構成です。

```env
# Truth Social
TRUTHSOCIAL_CLIENT_ID=your_client_id
TRUTHSOCIAL_CLIENT_SECRET=your_client_secret
TRUTHSOCIAL_USERNAME=your_username
TRUTHSOCIAL_PASSWORD=your_password

# Truth Social ブラウザフック用プロキシ
# 画像生成API用ではなく、Truth Social へのアクセスに使う
TS_HOOK_SERVER_BASE_URL=http://localhost:8000

# 通常会話用 LLM
OPENROUTER_API_KEY=your_openrouter_key
LLM_MODEL=openai/gpt-4.1-mini

# 動作設定
NOTIFICATION_POLL_SECONDS=20
NOTIFICATION_MAX_CONCURRENCY=8
GPU_TASK_MAX_CONCURRENCY=1
STATE_DB_PATH=history.db
```

画像生成は `IMAGE_BACKEND` で切り替えます。`IMAGE_MODEL` は API / ローカル共通で使用可能モデル一覧をカンマ区切りで指定し、先頭がデフォルトになります。`/image` や通常会話中の tool で `model` を明示した場合も、この一覧に含まれている必要があります。`IMAGE_MODEL` が未設定なら画像機能は無効です。

API バックエンドを使う例です。

```env
IMAGE_BACKEND=api

# 使用可能な画像モデル一覧。先頭がデフォルト
IMAGE_MODEL=gpt-image-1-mini,gpt-image-1

# OpenAI互換 Images API
IMAGE_API_STYLE=openai-images
IMAGE_API_URL=https://api.openai.com/v1/images/generations
IMAGE_API_KEY=your_image_api_key
```

ローカルの Python モジュールバックエンドを使う例です。

```env
IMAGE_BACKEND=stable-diffusion-cpp

# 使用可能な画像モデル一覧。先頭がデフォルト
IMAGE_MODEL=sdxl,anything-v5

# ローカル画像モデルモジュール配置先
IMAGE_MODELS_DIR=image_models
```

ローカルバックエンドでは、`IMAGE_MODELS_DIR` 配下に `sdxl.py` や `flux.py` のような Python モジュールを置きます。`IMAGE_MODEL=sdxl,flux` の場合、`image_models/sdxl.py` と `image_models/flux.py` が候補になります。

各モジュールは少なくとも `generate(request)` を定義してください。`generate` は `list[GeneratedImage]` または `list[bytes]` を返せます。同期関数でも非同期関数でも構いません。必要なら `cleanup()` を定義すると、生成後に呼ばれます。モデルやパイプラインの解放、サーバープロセスの停止、セッション終了などはその中で行ってください。

この方式なら、`stable-diffusion-cpp-python` を直接使う実装でも、`stable-diffusion.cpp` のサーバーモードに HTTP 接続する実装でも、各モデルモジュール内で自由に扱えます。CLI は使いません。

既存互換が必要な場合は、API バックエンドで `IMAGE_API_STYLE=openrouter-chat` を指定すると、従来の OpenRouter chat completions 形式も利用できます。

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

補足:
`model:` を省略した場合は `IMAGE_MODEL` の先頭が使われます。
`IMAGE_BACKEND=api` の場合、画像生成APIには `IMAGE_API_URL` / `IMAGE_API_KEY` で直接接続します。
`IMAGE_BACKEND=stable-diffusion-cpp` の場合、`image_models/<model>.py` を動的ロードして実行し、終了後に `cleanup()` とメモリ解放処理を行います。
`IMAGE_MODEL` やバックエンド設定が不足している場合、画像機能は無効になり、ダミー画像へのフォールバックは行いません。
重いローカルGPU処理の同時実行数は `GPU_TASK_MAX_CONCURRENCY` で制限します。通知処理全体の並列数は `NOTIFICATION_MAX_CONCURRENCY` で制御します。

### 動画生成 (`/video`)

```text
/video
model: luma

夕焼けの海岸線を走る馬
```

## 開発状況

- `outdated/`: 過去の実験コードや古い実装が含まれています（動作対象外）。
- `tests/`: 基本的な機能のテストコードを順次追加中。
