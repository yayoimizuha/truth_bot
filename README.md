# Truth Bot

SNS（Truth Social）向けのAIエージェントです。通知を監視し、メンションやリプライに対して自動で応答（テキスト、画像、動画）を行います。

## 特徴

- **自動応答専用**: 自発的な投稿やフォローは行わず、通知（メンション・リプライ）への応答に特化しています。
- **マルチモーダル対応**: 通常の会話だけでなく、コマンドによる直接的な画像・動画生成が可能です。
- **正規化処理**: SNS特有のHTMLタグ、メンション、短縮URLなどを適切に処理してAIに渡します。
- **会話履歴の考慮**: 対象の投稿から親投稿を辿り、文脈に沿った応答を生成します。
- **コマンドシステム**: 行頭コマンド（`/image_gen`, `/image_edit`, `/video`）により、詳細なパラメータ指定でのメディア生成が可能です。
- **外部メディア公開**: `MEDIA_HOST_API_URL` を設定すると、生成画像・動画を別サービスへ保存し、その公開URLを返信に含めます。

## システム構成

- **Agent Service (`timeline_agent.py`)**: 全体のエントリポイント。通知のポーリングと処理ループを管理します。
- **Browser Proxy (`ts_hook_server.py`)**: Truth SocialのCloudflare保護やログインを回避するためのブラウザベースのプロキシサーバー。
- **Media Host (`media_host_service/`)**: 生成画像・動画を保存し、公開ページと OGP を返す独立サービス。
- **SNS Agent (`sns_agent/`)**: コアロジックモジュール。
    - `service.py`: 処理フローのオーケストレーション。
    - `responder.py`: LLM（litellm / pydantic-ai）を使用した対話生成。
    - `commands.py`: `/image_gen`, `/image_edit`, `/video` コマンドのパース。
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

`media_host_service/` は依存とテストを分離しています。サービス単体のセットアップやテストは `media_host_service/` 直下で `uv sync` / `uv run python -m unittest discover -s tests` を実行してください。

### 環境設定

`.env` ファイルを作成し、必要な設定を記述してください。

まずは最小構成です。

```env
# Truth Social login for ts_hook_server.py
TRUTHSOCIAL_CLIENT_ID=your_client_id
TRUTHSOCIAL_CLIENT_SECRET=your_client_secret
TRUTHSOCIAL_USERNAME=your_username
TRUTHSOCIAL_PASSWORD=your_password

# Truth Social browser proxy
TS_HOOK_SERVER_BASE_URL=http://127.0.0.1:8000

# Conversation LLM
OPENROUTER_API_KEY=your_openrouter_key
LLM_MODEL=openai/gpt-4.1-mini

# Runtime
NOTIFICATION_POLL_SECONDS=20
NOTIFICATION_MAX_CONCURRENCY=8
GPU_TASK_MAX_CONCURRENCY=1
STATE_DB_PATH=history.db

# Optional media hosting service
MEDIA_HOST_API_URL=http://127.0.0.1:8010
MEDIA_HOST_UPLOAD_PASSWORD=shared_upload_password
```

画像生成は `IMAGE_BACKEND` で切り替えます。`IMAGE_MODEL` は API / ローカル共通で使用可能モデル一覧をカンマ区切りで指定し、先頭がデフォルトになります。`/image_gen`、`/image_edit`、通常会話中の tool で `model` を明示した場合も、この一覧に含まれている必要があります。`IMAGE_MODEL` が未設定なら画像機能は無効です。

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
IMAGE_MODEL=sdxl

# ローカル画像モデルモジュール配置先
IMAGE_MODELS_DIR=image_models
```

Z-Image Turbo を使う例です。

```env
IMAGE_BACKEND=stable-diffusion-cpp
IMAGE_MODEL=zimage_turbo
IMAGE_MODELS_DIR=image_models
```

Qwen-Image-Edit を使う例です。

```env
IMAGE_BACKEND=stable-diffusion-cpp
IMAGE_MODEL=qwen_image_edit
IMAGE_MODELS_DIR=image_models
```

モデル固有の重みパスは `image_models/.env.example` を参考に、ローカルの `image_models/.env.{実装名}` を作成して置きます。各実装は読み込み時にそのファイルを `load_dotenv(..., override=False)` で読みます。

```env
# image_models/.env.zimage_turbo
DIFFUSION_MODEL_PATH=/home/tomokazu/models/Z-Image-Turbo/z_image_turbo-Q6_K.gguf
LLM_PATH=/home/tomokazu/models/Z-Image-Turbo/Qwen3-4B-Instruct-2507-UD-Q6_K_XL.gguf
VAE_PATH=/home/tomokazu/models/Z-Image-Turbo/vae/diffusion_pytorch_model.safetensors
```

```env
# image_models/.env.qwen_image_edit
DIFFUSION_MODEL_PATH=/home/tomokazu/models/Qwen-Image-Edit-2511/qwen-image-edit-2511-Q4_K_M.gguf
LLM_PATH=/home/tomokazu/models/Qwen-Image-Edit-2511/Qwen2.5-VL-7B-Instruct-UD-Q5_K_XL.gguf
VAE_PATH=/home/tomokazu/models/Qwen-Image-Edit-2511/split_files/vae/qwen_image_vae.safetensors
LLM_VISION_PATH=/home/tomokazu/models/Qwen-Image-Edit-2511/mmproj-F16.gguf
```

補足:
- 実環境変数が同名で設定されていれば、`.env.{実装名}` よりそちらを優先します。

### 環境変数一覧

以下は実装が参照する環境変数の一覧です。

#### Truth Social / Proxy

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `TRUTHSOCIAL_CLIENT_ID` | `ts_hook_server.py` 利用時に必須 | なし | Truth Social OAuth クライアント ID |
| `TRUTHSOCIAL_CLIENT_SECRET` | `ts_hook_server.py` 利用時に必須 | なし | Truth Social OAuth クライアントシークレット |
| `TRUTHSOCIAL_USERNAME` | `ts_hook_server.py` 利用時に必須 | なし | Truth Social ログインユーザー名。返信履歴上で自分の発言判定にも利用 |
| `TRUTHSOCIAL_PASSWORD` | `ts_hook_server.py` 利用時に必須 | なし | Truth Social ログインパスワード |
| `TS_HOOK_SERVER_BASE_URL` | 任意 | `http://127.0.0.1:8000` | エージェントが接続するローカル browser proxy の URL |
| `TRUTHSOCIAL_BASE_URL` | 任意 | 空文字 | Truth Social API パスのベース URL を明示したい場合に使用。空なら proxy に相対パスで投げる |

#### 通常会話 LLM

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | 通常会話を使うなら必須 | なし | OpenRouter API キー |
| `LLM_API_KEY` | 任意 | なし | `OPENROUTER_API_KEY` の後方互換エイリアス |
| `LLM_MODEL` | 任意 | `openai/gpt-4.1-mini` | 通常会話のモデル名 |
| `LLM_HTTP_REFERER` | 任意 | なし | OpenRouter に送る `HTTP-Referer`。画像 API の一部ヘッダにも再利用 |
| `LLM_X_TITLE` | 任意 | なし | OpenRouter に送る `X-Title`。画像 API の一部ヘッダにも再利用 |
| `OPENROUTER_REASONING_EFFORT` | 任意 | なし | pydantic-ai 経由で OpenRouter reasoning effort を指定 |

#### エージェント動作設定

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `STATE_DB_PATH` | 任意 | `history.db` | 処理済み通知を記録する SQLite ファイル |
| `NOTIFICATION_POLL_SECONDS` | 任意 | `20` | 通知ポーリング間隔（秒） |
| `NOTIFICATION_MAX_CONCURRENCY` | 任意 | `8` | 通知処理の同時実行数 |
| `GPU_TASK_MAX_CONCURRENCY` | 任意 | `1` | 重いローカル GPU 処理の同時実行数 |
| `NOTIFICATION_FAILURE_MAX_RETRIES` | 任意 | `4` | 通知処理失敗時の最大再試行回数 |
| `NOTIFICATION_RETRY_BASE_SECONDS` | 任意 | `30.0` | 通知再試行の指数バックオフ初期値（秒） |
| `NOTIFICATION_RETRY_MAX_SECONDS` | 任意 | `600.0` | 通知再試行バックオフの上限（秒） |
| `SNS_MAX_POST_LENGTH` | 任意 | `5000` | 投稿テキストの最大長。超えた場合は末尾を省略して送信 |
| `MEDIA_HOST_API_URL` | 任意 | なし | 設定時、生成画像・動画は別サービスにアップロードされ、返信には公開URLを含めます |
| `MEDIA_HOST_UPLOAD_PASSWORD` | 任意 | なし | 設定時、`media_host_service` の `POST /media` に HTTP Basic 認証で同じ共有パスワードを送ります。公開ページや配信ファイルの閲覧には影響しません |

`media_host_service` は公開ページ `/m/{page_id}` とは別に、内容を JSON で返す `/api/pages/{page_id}` も提供します。SNS 側はこの JSON を使って hosted 画像・動画ポスターを会話履歴や `/image_edit` の参照メディアに展開します。

#### 画像生成共通

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `IMAGE_BACKEND` | 画像生成を使うなら必須 | `api` | `api` または `stable-diffusion-cpp`。`openrouter` 指定も内部で `api` 扱い |
| `IMAGE_MODEL` | 画像生成を使うなら必須 | なし | 使用可能モデル一覧。カンマ区切り、先頭がデフォルト |
| `IMAGE_MODELS_DIR` | ローカルバックエンド時は任意 | `image_models` | ローカル Python モジュールの探索ディレクトリ |

#### 画像生成 API バックエンド

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `IMAGE_API_STYLE` | 任意 | `openai-images` | `openai-images` または `openrouter-chat` |
| `IMAGE_API_URL` | 任意 | なし | OpenAI互換 Images API の URL。`openrouter-chat` では未指定時に OpenRouter の chat completions URL を使用 |
| `IMAGE_API_KEY` | API バックエンド時は通常必須 | なし | 画像 API 用キー |
| `OPENAI_API_KEY` | 任意 | なし | `IMAGE_BACKEND=api` かつ `IMAGE_API_STYLE=openai-images` のときの `IMAGE_API_KEY` 代替 |
| `IMAGE_API_MAX_RETRIES` | 任意 | `3` | 画像 API の最大再試行回数 |
| `IMAGE_API_RETRY_BASE_SECONDS` | 任意 | `1.0` | 画像 API 再試行バックオフ初期値（秒） |
| `IMAGE_API_RETRY_MAX_SECONDS` | 任意 | `8.0` | 画像 API 再試行バックオフ上限（秒） |
| `IMAGE_API_MODEL` | 非推奨 | なし | 旧来の単一画像モデル指定。実装上は後方互換用途 |
| `IMAGE_OPENROUTER_MODEL` | 非推奨 | `google/gemini-2.5-flash-image` | 旧来の OpenRouter 画像モデル名。実装上は後方互換用途 |

補足:
`IMAGE_API_MODEL` と `IMAGE_OPENROUTER_MODEL` はコード上に残っていますが、画像機能の有効化自体には `IMAGE_MODEL` が必要です。

#### 画像生成ローカルバックエンド共通キー

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `MODEL_PATH` | 条件付き必須 | なし | 単一ファイルのフルモデル |
| `DIFFUSION_MODEL_PATH` | モデル依存 | なし | diffusion model の GGUF パス |
| `CLIP_L_PATH` | 条件付き必須 | なし | 分離構成時の `clip_l` |
| `CLIP_G_PATH` | 条件付き必須 | なし | 分離構成時の `clip_g` |
| `LLM_PATH` | モデル依存 | なし | LLM の GGUF パス |
| `LLM_VISION_PATH` | モデル依存 | なし | vision projector の GGUF パス |
| `VAE_PATH` | 任意または必須 | なし | VAE の safetensors パス |

#### 画像生成ローカルバックエンド: SDXL

補足:
`MODEL_PATH` を使うか、`CLIP_L_PATH` と `CLIP_G_PATH` の両方を使ってください。

#### 画像生成ローカルバックエンド: Z-Image Turbo

必要な共通キー:
- `DIFFUSION_MODEL_PATH`
- `LLM_PATH`
- `VAE_PATH`

#### 画像生成ローカルバックエンド: Qwen-Image-Edit

必要な共通キー:
- `DIFFUSION_MODEL_PATH`
- `LLM_PATH`
- `VAE_PATH`
- `LLM_VISION_PATH`

#### 動画生成

| 変数名 | 必須 | デフォルト | 用途 |
| --- | --- | --- | --- |
| `VIDEO_API_URL` | 動画生成を使うなら必須 | なし | 動画生成 API エンドポイント |
| `VIDEO_API_KEY` | 動画生成を使うなら必須 | なし | 動画生成 API キー |
| `VIDEO_API_MODEL` | 任意 | なし | `/video` でモデル未指定時のデフォルトモデル |

ローカルバックエンドでは、`IMAGE_MODELS_DIR` 配下に `sdxl.py` や `flux.py` のような Python モジュールを置きます。`IMAGE_MODEL=sdxl,flux` の場合、`image_models/sdxl.py` と `image_models/flux.py` が候補になります。

各モジュールは少なくとも `generate(request)` を定義してください。`generate` は `list[GeneratedImage]` または `list[bytes]` を返せます。同期関数でも非同期関数でも構いません。必要なら `cleanup()` を定義できます。cleanup はローカル実行 1 回ごとの teardown で最大 1 回だけ呼ばれ、その後にモジュールのアンロードと `gc.collect()` / VRAM 解放がまとめて走ります。

最小例は `image_models/example.py` のとおりで、クラス継承は不要です。

この方式では `image_models/sdxl.py` が `stable-diffusion.cpp` の `sd-server` を都度起動し、`127.0.0.1` 上の OpenAI互換 `POST /v1/images/generations` を使って画像生成します。`sd-server` バイナリは次の順で自動探索します。

1. `/home/tomokazu/build/stable-diffusion.cpp/build/bin/sd-server`
2. `~/build/stable-diffusion.cpp/build/bin/sd-server`
3. `PATH` 上の `sd-server`

GPU は `pynvml` で空き状況を見て選び、処理全体は既存の `GPU_TASK_MAX_CONCURRENCY` 制御の内側で動きます。`pynvml` が使えない場合や NVML 初期化に失敗した場合は、そのままエラーとして扱います。`sd-server` の標準出力・標準エラーは親プロセスへそのまま流れるので、モデルロード失敗や起動失敗はログで追えます。

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

### 画像生成 (`/image_gen`)

```text
/image_gen
model: sdxl
size: 1024x1024
count: 1

宇宙で踊る猫のプロンプト
```

### 画像編集 (`/image_edit`)

```text
/image_edit
model: qwen_image_edit
cfg_scale: 2.5
flow_shift: 3
sampler: euler

Add flowers ring on the cat's head.
```

補足:
`model:` を省略した場合は `IMAGE_MODEL` の先頭が使われます。
`IMAGE_BACKEND=api` の場合、画像生成APIには `IMAGE_API_URL` / `IMAGE_API_KEY` で直接接続します。
`IMAGE_BACKEND=stable-diffusion-cpp` の場合、`image_models/<model>.py` を動的ロードして実行し、終了後にメモリ解放処理を行います。`sdxl` では `sd-server` を都度起動して OpenAI互換 API で生成します。
`/image_edit` は投稿または会話履歴に添付された画像を自動で取得し、それを `sd-server` の `image[]` に渡します。ファイルパス指定は不要です。
`IMAGE_MODEL` やバックエンド設定が不足している場合、画像機能は無効になり、ダミー画像へのフォールバックは行いません。
重いローカルGPU処理の同時実行数は `GPU_TASK_MAX_CONCURRENCY` で制限します。既定の `1` では画像生成リクエストは直列化されます。通知処理全体の並列数は `NOTIFICATION_MAX_CONCURRENCY` で制御します。

### 動画生成 (`/video`)

```text
/video
model: luma

夕焼けの海岸線を走る馬
```

## 開発状況

- `outdated/`: 過去の実験コードや古い実装が含まれています（動作対象外）。
- `tests/`: 基本的な機能のテストコードを順次追加中。
