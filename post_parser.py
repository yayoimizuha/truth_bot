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


if __name__ == '__main__':
    sample_texts = [
        """<p>このbotの使い方の説明を置いておきます。分からないことがあったら運用主( <span class="h-card"><a href="https://truthsocial.com/@yayoi_mizuha" class="u-url mention">@<span>yayoi_mizuha</span></a></span> ) までお問い合わせください。DMの自動返信機能はありません。生成したコンテンツの一切の責任について当botは保証しませんのでご注意ください。<br/>運用主のポケットマネーで動いていますので、DDoSその他問題が発生した場合はサ終することがあります。</p>""",
        """<p><span class="quote-inline"><br/>RT: https://truthsocial.com/users/mizuha_bot/statuses/113933999830884759</span>Pin</p>""",
        """<p><span class=\"h-card\"><a href=\"https://truthsocial.com/@mizuha_bot\" class=\"u-url mention\">@<span>mizuha_bot</span></a></span> <a href=\"https://github.com/yayoimizuha/youtube-viewcount-logger-rust\" rel=\"nofollow noopener noreferrer\" target=\"_blank\"><span class=\"invisible\">https://</span><span class=\"ellipsis\">github.com/yayoimizuha/youtube</span><span class=\"invisible\">-viewcount-logger-rust</span></a> よよおお</p>"""
    ]

    for text in sample_texts:
        print(text)
        print(post_parser(text))
