"""社交来源登录凭据的结构校验，不请求平台或记录凭据内容。"""

from http.cookies import CookieError, SimpleCookie


def parse_x_search_cookies(raw: str) -> dict[str, str]:
    """仅保留 X 请求需要的登录与 CSRF Cookie；非法或不完整输入不可用。"""
    if not raw.strip() or any(ord(character) < 32 and character != "\t" or ord(character) > 126 for character in raw):
        return {}
    cookies: SimpleCookie = SimpleCookie()
    try:
        cookies.load(raw.strip())
    except CookieError:
        return {}
    selected = {name: cookies[name].value for name in ("auth_token", "ct0") if name in cookies and cookies[name].value}
    if len(selected) != 2 or any(
        any(ord(character) < 33 or ord(character) > 126 or character in ';,"\\' for character in value)
        for value in selected.values()
    ):
        return {}
    return selected
