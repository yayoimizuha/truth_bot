import json
import re

from bs4 import BeautifulSoup, PageElement, Tag, NavigableString


def post_parser(_text: str) -> str:
    _html = BeautifulSoup(_text, 'lxml')

    def recursive_extractor(elm: PageElement) -> str:
        match elm:
            case NavigableString():
                # print(elm, type(elm))
                return elm.get_text(strip=True)
            case Tag():
                # print(elm, type(elm))
                match elm.name, elm.attrs.get("class", []):
                    case "br", _:
                        return "\n"
                    case "span", ["quote-inline"]:
                        return f' quote:{re.search("/statuses/(\\d+)", elm.get_text(strip=True)).group(1)} '
                    case "span", _:
                        return f' {elm.get_text(strip=True)} '
                    case _, _:
                        return "".join(list(map(recursive_extractor, elm.children)))
            case _:
                return ""

    return recursive_extractor(_html).strip(" \n")



# ── ボットリプライ用マーカー定数 ──────────────────────────────
BOT_ERROR_MARKER = "[BOT_ERROR]"


def parse_llm_syntax(text: str) -> dict:
    """
    投稿テキストを解析し、コマンド構文・ボットマーカーを識別する。

    全角スペース、全角＠、およびヘッダ内の全角コロン・イコールに対応。
    加えて、ボット自身が投稿した [BOT_ERROR] マーカーも検出する。

    Returns (いずれも dict):
        ボットエラー投稿の場合:
            {"type": "bot_error", "content": "エラー本文"}
        ユーザーコマンド投稿の場合:
            {"type": "naive" | "image_gen" | ..., "options": {...}, "prompt": "..."}
        コマンドなし（通常テキスト）の場合:
            {"type": "naive"}
    """

    # ── 1. ボットエラーマーカーの検出 ─────────────────────────
    if text.startswith(BOT_ERROR_MARKER):
        return {"type": "bot_error", "content": text[len(BOT_ERROR_MARKER):].strip()}

    # ── 2. ユーザーコマンド構文の検出 ─────────────────────────
    # ^                 : 行頭
    # \s*               : 先頭の空白（全角・半角・タブ含む）を許容
    # (?: ... )*        : メンションブロックの繰り返し（0回以上）
    #   [@＠]           : 半角@ または 全角＠
    #   \S+             : 空白以外の文字（ユーザー名）
    #   \s+             : 区切り空白（全角・半角対応）
    # \[([^\]]+)\]      : [] で囲まれたヘッダ部分

    pattern = r'^\s*(?:[@＠]\S+\s+)*\[([^\]]+)\]'

    match = re.match(pattern, text)

    # マッチしない（文中の[]や、ヘッダなし）場合は naive
    if not match:
        return {"type": "naive"}

    # データの抽出
    raw_header_content = match.group(1)

    # プロンプト本文の抽出（マッチした箇所の後ろすべて）
    prompt_part = text[match.end():]

    # ヘッダ内部の正規化（揺らぎ吸収）
    # 日本語入力では内部の区切り文字も全角になりがちなので、半角に置換して処理しやすくする
    # 例: "gen：seed＝123" -> "gen:seed=123"
    normalized_header = raw_header_content.replace('：', ':').replace('＝', '=')

    # Type と Options の解析
    parts = normalized_header.split(':')
    msg_type = parts[0]

    options = {}
    if len(parts) > 1:
        for segment in parts[1:]:
            # 値の中に `=` が含まれる可能性を考慮し、最初の1つだけ分割
            if '=' in segment:
                key, value = segment.split('=', 1)
                options[key] = value

    return {
        "type": msg_type,
        "options": options,
        "prompt": prompt_part
    }


# --- 動作確認テスト ---


if __name__ == '__main__':
    sample_texts = [
        """<p>このbotの使い方の説明を置いておきます。分からないことがあったら運用主( <span class="h-card"><a href="https://truthsocial.com/@yayoi_mizuha" class="u-url mention">@<span>yayoi_mizuha</span></a></span> ) までお問い合わせください。DMの自動返信機能はありません。生成したコンテンツの一切の責任について当botは保証しませんのでご注意ください。<br/>運用主のポケットマネーで動いていますので、DDoSその他問題が発生した場合はサ終することがあります。</p>""",
        """<p><span class="quote-inline"><br/>RT: https://truthsocial.com/users/mizuha_bot/statuses/113933999830884759</span>Pin</p>""",
        """<p><span class=\"h-card\"><a href=\"https://truthsocial.com/@mizuha_bot\" class=\"u-url mention\">@<span>mizuha_bot</span></a></span> <a href=\"https://github.com/yayoimizuha/youtube-viewcount-logger-rust\" rel=\"nofollow noopener noreferrer\" target=\"_blank\"><span class=\"invisible\">https://</span><span class=\"ellipsis\">github.com/yayoimizuha/youtube</span><span class=\"invisible\">-viewcount-logger-rust</span></a> よよおお</p>"""
    ]

    for text in sample_texts:
        print(text)
        print(post_parser(text))

    test_cases = [
        """@mizuha_bot [image_gen:seed=123] cat""",
        """　＠mizuha_bot　[edit]　背景を削除""",
        """@mizuha_bot [image_gen：seed＝123：step＝30] 高画質""",
        """＠user1　＠user2　[naive]　こんにちは""",
        """これはテストです　[重要]　なポイント""",
        # ボットマーカーのテストケース
        """[BOT_ERROR] Traceback (most recent call last): ...""",
    ]


    for prompt in test_cases:
        result = parse_llm_syntax(prompt)
        print(f"Input : {prompt.strip()}")
        print(json.dumps(result, ensure_ascii=False, indent=4))
        print("-" * 40)
