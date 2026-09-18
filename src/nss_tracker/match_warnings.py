"""試合レコードの、ランク以外の異常値を検出する判定ロジック(Issue #441)。

Issue #433(テニスのVS画面をサッカーの試合開始と誤検知)は、DB上の値を見比べるだけで
異常だと分かった。同じ配信の他の試合と比べて「自チームのVS画面ランクが4人とも
読めていない」という点が明らかな外れ値だった。

全セッションのログから「VS画面ランク」の未読スロット数(自チーム4+相手チーム4=8人中)を
集計した結果(2026-09-18時点の実測):

    未読数  件数  内訳
    0      68件  通常の試合
    1       8件  B〜E帯など1人だけ未識別(正常)
    7       1件  Issue #433のテニス誤検知そのもの
    8      20件  ランクバッジが一切表示されない試合(専用部屋・ランクを賭けない試合、正常)

2〜6人未読のケースは実データに1件も無く、異常値(7人未読)と正常値(0〜1人未読・
8人未読)の間に大きな空きがある。そのため:

- 8人とも未読(known_total=0)は対象外にする(バッジが最初から出ない試合の正常系
  そのものであり、専用部屋・ランクを賭けない試合で頻繁に起こる)
- 1人以上読めているのに2人以上未読(known_total 1〜6)を警告対象にする

チームカラーによる判定(青系/ピンク系どちらでもない値)も候補に挙がったが、ランクを
賭けない試合・専用部屋で正当な理由でチームカラーが芝生の色になるケースがあり
誤検知が避けられないため不採用にした(ユーザーとの相談で決定、detection/team_color.pyの
モジュールdocstring・CLAUDE.mdのIssue #424の節参照)。

`rank_warnings.py`とは責務を分け、DB/Webに依存しない純粋な判定に留める(呼び出し元が
DBから値を集めて渡す)。rank_before/rank_afterがNoneの試合(ランクを賭けない試合)でも
判定できる必要がある(#433自体がそうだったため)ため、`rank_warnings.evaluate()`の
早期return(rank_before/afterがNoneなら空リスト)とは独立させている。

確認済み(ack)は既存の`match_rank_warning_acks`(rank_warnings.pyと共有)テーブルを
そのまま流用する。ルールコードは既存(A/C/D/E/H/I/J)と衝突しないよう'K'を使う。
"""

from dataclasses import dataclass

# 8人(自チーム4+相手チーム4)のうち、これ以上未読なら警告対象にする下限。
# 実データでは0〜1人未読(正常)と8人未読(バッジ非表示の正常系)の間に、
# 7人未読(Issue #433の異常値)しか存在しない。2〜6人未読の実測は無いが、
# 正常値との間に大きな空きがあるためこの範囲を丸ごと警告対象にする
# (モジュールdocstring参照)。
MIN_UNREAD_SLOTS = 2

RULE_CODES = ("K",)


@dataclass(frozen=True)
class MatchWarning:
    """1件の警告。`rule_code`は確認済み(acknowledge)の記録単位でもある。"""

    rule_code: str
    message: str
    acknowledged: bool = False


def evaluate(
    *,
    mine_known_count: int,
    opponent_known_count: int,
    acknowledged_rule_codes: frozenset[str] = frozenset(),
) -> list[MatchWarning]:
    """1試合分の警告を返す。

    `mine_known_count`/`opponent_known_count`は、`web/server.py`の
    `_summarize_vs_slot_ranks()`が返す「読み取れた人数」(0〜4)。VS画面自体を
    見逃した試合(vs_slot_ranksが1行も無い試合)もmine_known_count=
    opponent_known_count=0として渡ってくるため、known_total=0を除外する下記の
    条件により自動的にスキップされる(モジュールdocstring参照)。
    """
    known_total = mine_known_count + opponent_known_count
    unread_total = 8 - known_total

    found: list[tuple[str, str]] = []
    if known_total > 0 and unread_total >= MIN_UNREAD_SLOTS:
        found.append(
            (
                "K",
                f"VS画面のランク表示が8人中{unread_total}人読み取れていません"
                f"(自チーム{4 - mine_known_count}人・相手チーム{4 - opponent_known_count}人未読)。"
                "ランクを賭けた通常の試合でここまで多く未読になることは無いため、"
                "サッカー以外の試合を誤って記録した可能性があります。",
            )
        )

    return [
        MatchWarning(rule_code=code, message=message, acknowledged=code in acknowledged_rule_codes)
        for code, message in found
    ]
