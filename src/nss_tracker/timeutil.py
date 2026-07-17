"""JST(日本標準時)関連のユーティリティ。

個人利用(日本国内での配信・記録)を前提とし、DB・ログともにUTCではなくJSTで
統一する。日本にサマータイムは無いため、`zoneinfo`等のタイムゾーンデータベースに
依存せず、UTC+9固定オフセットで正しく表現できる。
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def now_jst() -> datetime:
    """現在時刻をJSTのタイムゾーン付きdatetimeで返す。"""
    return datetime.now(JST)
