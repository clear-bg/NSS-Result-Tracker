"""手動入力されたランク値の矛盾を検出する判定ロジック(Issue #407)。

2026-09-08に、DBに記録済みのランク値を配信映像と突き合わせて検証したところ、
`/rank-entry`の手動入力値に4件の誤りが見つかった(matches id=1, 3, 15, 21)。
特にid=15は`42.28`を`41.28`と入力したことで存在しない降格が記録され、その
巻き添えでid=16に存在しない昇格まで記録されていた。いずれも「勝ったのにランクが
減っている」「帯が動いているのに昇格/降格の演出を検知していない」といった、
**既にDBにある値だけで機械的に判定できる矛盾**として現れていたため、入力直後に
その場で気付けるようにする。

この関数群はDBにもWebにも依存しない純粋な判定に留める(呼び出し元がDBから値を
集めて渡す)。`/rank-entry`(Issue #407)と、DB全体を対象にした健全性チェック一覧
(Issue #408)の両方から同じロジックを使うため。

採用したルールは、いずれも「ゲーム仕様上・ログ上ありえない矛盾」だけを対象にする:

- A: 勝敗と増減の符号が矛盾している
- C: 帯番号の変化と昇格/降格ラベルの検知結果が矛盾している
- D: 1試合で2帯以上動いている
- E: 勝敗と帯変化が矛盾している(勝ったのに降格/負けたのに昇格)
- I: 次の試合のVS画面で読んだ自分の帯と一致しない
- J: 両チームの合計ランク差が小さいのに変化量がゼロ
- H: 次の試合のバッジ読み取り値と乖離している(Issue #408の一覧ページ専用)

**変化量ゼロ(Δ=0)そのものは警告対象にしない(ルールAの重要な前提)。** 自チームと
相手チームの合計ランク差が大きい場合、および試合中に味方が抜けて3人になり人数差が
あった場合は、勝っても負けてもゲージが全く動かないことがある(いずれも2026-09-08に
ユーザー確認済み)。実データではid=11(lose, 42.20→42.20)、id=16(win, 42.28→42.28)、
id=17(lose, 42.28→42.28)がいずれも配信映像で確認済みの正しい記録だった。
符号が逆になっている場合だけを矛盾として扱う。

ルールJはこのうち「合計ランク差が大きい」ケースだけを条件として持つ。もう一方の
理由(味方が抜けて3人)は**チームの人数がDB上のどこにも記録されていないため判定
できない**(id=11がまさにこのケースで、ルールJでは誤検知になる)。そのため警告は
人間が確認したうえで消せる前提で設計してあり、確認済みの記録は
`database/db.py`の`match_rank_warning_acks`テーブルが持つ。

ルールHはIssue #408の健全性チェック一覧ページ専用で、`/rank-entry`(#407)からは
呼ばない。次の試合が記録されて初めて判定できるため、入力直後に警告を出す
`/rank-entry`では意味を成さないという理由による(呼び出し元が
`next_match_rank_before_ocr`を渡さなければ自動的にスキップされる)。
"""

from dataclasses import dataclass
from typing import Optional

# ルールJ: 「合計ランク差が小さいのにΔ=0」とみなす境界。
# 2026-09-08の実データ25試合の実測に基づく:
#   - Δ=0だった3試合の合計ランク差: id=11が0、id=17が-20、id=16は未読スロットありで判定対象外
#   - Δ≠0だった22試合の|合計ランク差|: 0〜5が17件、6〜9が1件、10〜16が4件、20以上は0件
# Δ≠0の試合では|差|が最大16に留まる一方、Δ=0で説明のつくid=17は20だったため、
# この間を取って20とした。ただしΔ=0のサンプルが実質2件しかなく根拠としては弱いため、
# Δ=0の試合がある程度溜まった時点で実測し直すこと(Issue #407のコメント参照)。
TEAM_RANK_DIFF_THRESHOLD = 20

# ルールJの判定に必要なスロット数。片方でも未読スロットがあると合計が過小に出るため、
# 両チームとも4スロット全て読めている試合だけを判定対象にする(1人平均に換算して
# 補正する案は、未読スロットが実際には低ランク(B〜E帯)であることが多く、補正すると
# チームを過大評価して逆に誤検知を生むため不採用。ユーザーとの相談で決定)
_REQUIRED_SLOT_COUNT = 4

# ルールH: 「次の試合のバッジ読み取り値と乖離している」とみなす境界。
# 2026-09-08の実データ25試合の実測に基づく。手動入力値と次の試合の
# rank_before_ocr(結果バナー直後に読んだコンパクトバッジ)は、値が正しければ
# ±0.01で一致していた(24組中11組)。一方で誤入力だったid=1は0.39、id=15は1.00
# 離れていた。両者の間を取り、正しい試合を巻き込まない0.3にしてある
# (0.1まで下げると正しい試合が9件誤検知になる、Issue #407のコメント参照)。
NEXT_BADGE_GAP_THRESHOLD = 0.3

RULE_CODES = ("A", "C", "D", "E", "H", "I", "J")

_LEAGUE_CHANGE_LABELS = {"up": "昇格演出", "down": "降格ラベル"}


@dataclass(frozen=True)
class RankWarning:
    """1件の警告。`rule_code`は確認済み(acknowledge)の記録単位でもある。"""

    rule_code: str
    message: str
    acknowledged: bool = False


@dataclass(frozen=True)
class TeamRankTotals:
    """ルールJ用に、1試合のVS画面から集計した両チームの合計ランク(統一スケール)。

    `mine`/`opponent`は`web/server.py`の`_summarize_vs_slot_ranks()`が返す合計値
    (∞/S/A帯を跨いで比較できる統一スケール、Issue #100)。読み取れなかった
    スロットがある場合に判定自体をスキップできるよう、集計に含められた人数
    (`mine_known_count`/`opponent_known_count`)もあわせて受け取る。
    """

    mine: Optional[int]
    opponent: Optional[int]
    mine_known_count: int
    opponent_known_count: int

    def diff(self) -> Optional[int]:
        """両チームとも4スロット読めている場合のみ、合計の差を返す。"""
        if self.mine is None or self.opponent is None:
            return None
        if self.mine_known_count != _REQUIRED_SLOT_COUNT:
            return None
        if self.opponent_known_count != _REQUIRED_SLOT_COUNT:
            return None
        return self.mine - self.opponent


def _tier_direction(tier_before: int, tier_after: int) -> Optional[str]:
    if tier_after > tier_before:
        return "up"
    if tier_after < tier_before:
        return "down"
    return None


def evaluate(
    *,
    result: str,
    rank_before: Optional[float],
    rank_after: Optional[float],
    league_change_label_detected: Optional[str] = None,
    next_match_vs_tier: Optional[int] = None,
    next_match_rank_before_ocr: Optional[float] = None,
    team_rank_totals: Optional[TeamRankTotals] = None,
    acknowledged_rule_codes: frozenset[str] = frozenset(),
) -> list[RankWarning]:
    """1試合分の警告を、ルールコード順(RULE_CODES順)で返す。

    `rank_before`/`rank_after`のいずれかがNoneの試合(ランクを賭けていない、または
    まだ手動入力していない)は判定できないため、常に空リストを返す。

    `next_match_vs_tier`は次の試合のVS画面で読んだ自分の帯番号(∞帯のみ、
    未検知ならNone)。`next_match_rank_before_ocr`は次の試合の結果バナー直後に
    読んだバッジの値(ルールH、Issue #408の一覧ページからのみ渡す)。
    `team_rank_totals`はこの試合のVS画面から集計した両チームの合計ランク。
    いずれも無ければ対応するルールをスキップする。

    ルールHは`next_match_vs_tier`が分かっている場合、**次の試合のバッジ読み取り値の
    帯がVS画面の帯と一致するときだけ**判定に使う。バッジOCRは帯番号を誤読すること
    があり(実データでid=24が43.00と読めていた)、ガード無しだとその誤読でそのまま
    誤検知するため(Issue #408のコメント参照)。

    `acknowledged_rule_codes`に含まれるルールは`acknowledged=True`にして返す
    (呼び出し元が非表示にする)。判定自体は行うため、値を修正して矛盾が解消されれば
    そのルールは自然と一覧から消える。
    """
    if rank_before is None or rank_after is None:
        return []

    delta = round(rank_after - rank_before, 2)
    tier_before = int(rank_before)
    tier_after = int(rank_after)
    tier_direction = _tier_direction(tier_before, tier_after)
    rank_text = f"{rank_before} → {rank_after}"

    found: list[tuple[str, str]] = []

    # A: 勝敗と増減の符号が矛盾している(Δ=0は正常なので対象にしない、docstring参照)
    if result == "win" and delta < 0:
        found.append(("A", f"勝ちですがランクが下がっています({rank_text})。"))
    elif result == "lose" and delta > 0:
        found.append(("A", f"負けですがランクが上がっています({rank_text})。"))
    elif result == "draw" and delta != 0:
        found.append(("A", f"引き分けですがランクが変化しています({rank_text})。"))

    # C: 帯番号の変化と昇格/降格ラベルの検知結果が矛盾している
    if tier_direction != league_change_label_detected:
        if tier_direction is None:
            label = _LEAGUE_CHANGE_LABELS[league_change_label_detected]
            found.append(("C", f"{label}を検知していますが、入力値では帯が{tier_before}のまま変わっていません。"))
        elif league_change_label_detected is None:
            expected = _LEAGUE_CHANGE_LABELS["up" if tier_direction == "up" else "down"]
            found.append(
                ("C", f"入力値では帯が{tier_before}→{tier_after}に変わっていますが、{expected}を検知していません。")
            )
        else:
            label = _LEAGUE_CHANGE_LABELS[league_change_label_detected]
            found.append(
                ("C", f"{label}を検知していますが、入力値では帯が{tier_before}→{tier_after}と逆に動いています。")
            )

    # D: 1試合で2帯以上動いている(ゲーム仕様上ありえない、Issue #136)
    if abs(tier_after - tier_before) >= 2:
        found.append(("D", f"1試合で帯が2つ以上変化しています({tier_before} → {tier_after})。"))

    # E: 勝敗と帯変化が矛盾している(ゲーム仕様上ありえない)
    if result == "win" and tier_direction == "down":
        found.append(("E", f"勝ちですが降格しています(帯 {tier_before} → {tier_after})。"))
    elif result == "lose" and tier_direction == "up":
        found.append(("E", f"負けですが昇格しています(帯 {tier_before} → {tier_after})。"))

    # I: 次の試合のVS画面で読んだ自分の帯と一致しない
    if next_match_vs_tier is not None and next_match_vs_tier != tier_after:
        found.append(
            (
                "I",
                f"次の試合のVS画面では帯が{next_match_vs_tier}でしたが、"
                f"入力値は帯{tier_after}({rank_after})です。",
            )
        )

    # H: 次の試合のバッジ読み取り値と乖離している(Issue #408の一覧ページ専用)
    if next_match_rank_before_ocr is not None:
        # バッジOCRの帯番号の誤読をそのまま拾わないよう、VS画面の帯と突き合わせる
        badge_tier_is_trusted = next_match_vs_tier is None or int(next_match_rank_before_ocr) == next_match_vs_tier
        gap = round(abs(next_match_rank_before_ocr - rank_after), 2)
        if badge_tier_is_trusted and gap >= NEXT_BADGE_GAP_THRESHOLD:
            found.append(
                (
                    "H",
                    f"次の試合で読み取ったランク({next_match_rank_before_ocr})と{gap}離れています。"
                    f"試合の間にランクは変動しないため、どちらかが誤っている可能性があります。",
                )
            )

    # J: 両チームの合計ランク差が小さいのに変化量がゼロ
    if delta == 0 and team_rank_totals is not None:
        diff = team_rank_totals.diff()
        if diff is not None and abs(diff) < TEAM_RANK_DIFF_THRESHOLD:
            found.append(
                (
                    "J",
                    f"両チームの合計ランク差が小さい({diff})にもかかわらず、ランクが変化していません"
                    f"({rank_after})。味方が抜けて人数差があった試合であれば正常です。",
                )
            )

    order = {code: index for index, code in enumerate(RULE_CODES)}
    found.sort(key=lambda item: order[item[0]])
    return [
        RankWarning(rule_code=code, message=message, acknowledged=code in acknowledged_rule_codes)
        for code, message in found
    ]
