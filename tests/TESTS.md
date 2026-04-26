# Test Overview

このディレクトリには、Truth Bot 本体の主要コンポーネントごとのユニットテストがあります。

## `test_commands.py`

`/image_gen` / `/image_edit` コマンドのパースと画像生成まわりの基本動作を確認します。

- `/image_gen` ヘッダの正常パース
- `/image_edit` ヘッダの正常パース
- 不正ヘッダ行の拒否
- `count > 4` の拒否
- API バックエンドで OpenAI 互換 Images API 形式のリクエストになること
- `IMAGE_BACKEND=openrouter` が内部的に API バックエンド扱いになること
- ローカル画像モデルで `IMAGE_MODEL` の先頭がデフォルトになること
- ローカル画像モデルで許可されていないモデル名を拒否すること
- ローカル画像モデルモジュールの `generate()` と `cleanup()` が呼ばれること
- `IMAGE_MODEL` 未設定時に画像機能が無効になること

## `test_publisher.py`

返信投稿テキストの組み立てを確認します。

- 先頭メンションと返信本文中メンションの prefix 化
- メンション順序の維持
- 重複メンションの扱い

## `test_normalizer.py`

Truth Social の投稿 HTML を内部表現へ正規化できることを確認します。

- メンション抽出
- 改行付きテキストの正規化
- URL 展開結果の保持

## `test_ts_hook_server.py`

`ts_hook_server.py` のブラウザプロキシ内部処理を確認します。

- 初回 `goto()` 後にページロード待ちを行うこと
- `reload()` 後にページロード待ちを行うこと
- `_raw_fetch()` が evaluate 失敗時に 1 回だけリトライすること
- 2 回連続失敗時は例外を送出すること

## `test_image_models.py`

`image_models` パッケージの共通基盤を確認します。

- `bytes` から `GeneratedImage` への正規化
- GPU/VRAM 統計表示文字列の整形
- `pynvml` 未導入時に GPU 統計取得が空配列で安全に失敗すること

## `test_gpu_tasks.py`

GPU タスク用セマフォの並列制御を確認します。

- `GPUTaskLimiter(1)` で 2 タスクが直列実行されること

## `test_service.py`

通知処理の並列化と終了処理を確認します。

- `poll_once()` が通知を待たずに `create_task()` で起動すること
- in-flight 中の同一通知 ID を重複起動しないこと
- `aclose()` で実行中通知タスクを cancel し、内部状態を掃除すること

## 実行方法

全テスト:

```bash
python -m unittest tests.test_commands tests.test_publisher tests.test_normalizer tests.test_ts_hook_server tests.test_image_models tests.test_gpu_tasks tests.test_service
```

個別実行例:

```bash
python -m unittest tests.test_service
```

`media_host_service/` のテストはサービス配下に分離されています。サービス側の依存だけで実行する場合は、`media_host_service/` 直下で次を使ってください。

```bash
uv run python -m unittest discover -s tests
```
