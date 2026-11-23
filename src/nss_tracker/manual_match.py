"""記録漏れ試合の手動追加(Issue #489)の入力値の検証と挿入位置の判定。

システム(`main.py`)の起動を忘れたままランクを賭けた試合をすると、その試合は記録されない。
たいていは2試合目以降で起動に気づくため、抜けるのは配信の最初の1試合であることが多い。
これまではDBへ直接書き込んで補っていたが、`/match-entry`から自分で追加できるようにした。

このモジュールはDBにもWebにも依存しない純粋な処理に留める(`rank_warnings.py`と同じ方針)。
フォーム値の検証(`parse_form`)と、試合終了時刻から挿入位置を決める処理
(`decide_insert_position`)を持ち、実際の書き込みは`database/db.py`の
`insert_manual_match()`が、画面は`web/server.py`の`/match-entry`が担う。

挿入位置は配信を選ばせず、試合終了時刻から自動で決める(ユーザーとの相談で決定)。
`sessions`は`main.py`の起動単位で、配信の途中で再起動すると分かれたり、試合が0件の
セッションがあったりして「配信」とは一致しないため:

- 入力時刻より後にある最初の試合の**直前**に挿入し、その試合と同じセッションに属させる
- 挿入できるのは、その「直後の試合」が**セッションの1試合目である場合だけ**。
  起動を忘れるのは配信の最初なので、実際の使い方と合っており、位置を誤る余地も小さい
- 後ろに試合が無い(起動後まだ1試合もしていない)場合は末尾に追加し、現在のセッションに属させる
- 「試合時刻がセッションの起動時刻より前」という条件は付けない。2026-10-06のように、
  起動済みだが仮想カメラの開始が遅れて記録できなかったケースがあるため
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Optional, Sequence

from nss_tracker import rank_warnings
from nss_tracker.timeutil import JST

RESULTS = ("win", "lose", "draw")

# VS画面のスロット数(1チームあたり)と、帯ごとの帯内数値の範囲。
# ∞は0〜49、Sは0〜9、Aは最大29(下限はゲーム仕様上不明だが負にはならない)。
# rank_estimate.RANK_TIER_OFFSETSと同じユーザー確認済みの仕様による
VS_SLOT_COUNT = 4
VS_TIER_VALUE_RANGES = {"∞": (0, 49), "S": (0, 9), "A": (0, 29)}
VS_SIDES = ("mine", "opponent")
# フォームで「不明」を表す帯ラベルの値
VS_UNKNOWN_LABEL = "unknown"

# 書き込み前に入力エラーとして弾くルール(rank_warningsのルールコード)。
# いずれもゲーム仕様上ありえない矛盾のため、警告ではなくエラーにする
BLOCKING_RULE_CODES = ("A", "D", "E")


class ManualMatchError(ValueError):
    """入力値・挿入位置が条件を満たさない。`field`はエラーを表示する入力欄の名前。"""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name


@dataclass(frozen=True)
class VsSlotEntry:
    """VS画面の1スロット。帯・数値ともNoneなら「不明」(B〜E帯・読み取れなかった人と同じ扱い)。"""

    label: Optional[str] = None
    value: Optional[int] = None

    @property
    def is_unknown(self) -> bool:
        return self.label is None


@dataclass(frozen=True)
class ManualMatchEntry:
    """フォームから読み取った、追加する試合の内容。"""

    detected_at: datetime
    result: str
    rank_after: float
    player_shortage: bool = False
    vs_mine: tuple[VsSlotEntry, ...] = field(default_factory=lambda: (VsSlotEntry(),) * VS_SLOT_COUNT)
    vs_opponent: tuple[VsSlotEntry, ...] = field(default_factory=lambda: (VsSlotEntry(),) * VS_SLOT_COUNT)

    @property
    def has_vs_ranks(self) -> bool:
        """1人でもランクが分かっているか。全員不明ならvs_slot_ranksを保存しない。"""
        return any(not slot.is_unknown for slot in self.vs_mine + self.vs_opponent)


@dataclass(frozen=True)
class MatchRef:
    """挿入位置の判定に使う、既存の試合の最小限の情報。"""

    id: int
    detected_at: datetime
    session_id: Optional[int]


@dataclass(frozen=True)
class InsertPosition:
    """挿入位置。`following`が無い場合は末尾に追加する(`new_id`はNone=自動採番)。"""

    session_id: int
    previous: Optional[MatchRef]
    following: Optional[MatchRef]

    @property
    def new_id(self) -> Optional[int]:
        return self.following.id if self.following is not None else None


def _parse_int(text: str, field_name: str, label: str, minimum: int, maximum: int) -> int:
    text = text.strip()
    if not text.isdigit():
        raise ManualMatchError(field_name, f"{label}は{minimum}〜{maximum}の整数で入力してください。")
    value = int(text)
    if not minimum <= value <= maximum:
        raise ManualMatchError(field_name, f"{label}は{minimum}〜{maximum}の範囲で入力してください。")
    return value


def parse_detected_at(date_text: str, hour: str, minute: str, second: str, now: datetime) -> datetime:
    """試合終了時刻(分まで必須・秒は任意)を読み取る。未来の時刻は弾く。"""
    try:
        day = date.fromisoformat(date_text.strip())
    except ValueError:
        raise ManualMatchError("detected_at", "日付を入力してください。") from None
    hour_value = _parse_int(hour, "detected_at", "時", 0, 23)
    minute_value = _parse_int(minute, "detected_at", "分", 0, 59)
    second_value = _parse_int(second, "detected_at", "秒", 0, 59) if second.strip() else 0
    detected_at = datetime(day.year, day.month, day.day, hour_value, minute_value, second_value, tzinfo=JST)
    if detected_at > now:
        raise ManualMatchError("detected_at", "未来の時刻は入力できません。")
    return detected_at


def parse_rank(tier_text: str, fraction_text: str) -> float:
    """帯番号と小数部からランク値を作る。

    小数部は`/rank-entry`(Issue #457)と同じく「小数点以下の桁をそのまま並べたもの」として
    読む("5"なら.50、"05"なら.05)。
    """
    tier_text = tier_text.strip()
    fraction_text = fraction_text.strip()
    if not tier_text.isdigit():
        raise ManualMatchError("rank_after", "ランクの帯番号を0以上の整数で入力してください。")
    if not fraction_text.isdigit() or len(fraction_text) > 2:
        raise ManualMatchError("rank_after", "ランクの小数部を2桁までの数字で入力してください。")
    hundredths = int(fraction_text.ljust(2, "0"))
    return round(int(tier_text) + hundredths / 100, 2)


def parse_vs_slot(label: str, value: str, field_name: str, who: str) -> VsSlotEntry:
    """VS画面の1スロットを読み取る。帯が「不明」か数値が空欄なら不明として扱う。"""
    label = label.strip()
    value = value.strip()
    if label == VS_UNKNOWN_LABEL or value == "":
        return VsSlotEntry()
    if label not in VS_TIER_VALUE_RANGES:
        raise ManualMatchError(field_name, f"{who}の帯が不正です。")
    minimum, maximum = VS_TIER_VALUE_RANGES[label]
    return VsSlotEntry(label=label, value=_parse_int(value, field_name, f"{who}の{label}帯の数値", minimum, maximum))


def vs_field_name(side: str, index: int) -> str:
    return f"vs_{side}_{index}"


def vs_slot_name(side: str, index: int) -> str:
    """画面に出すスロットの呼び名(自チームのスロット0は自分自身)。"""
    if side == "mine":
        return "自分" if index == 0 else f"味方{index}"
    return f"相手{index + 1}"


def parse_form(form: Mapping[str, str], now: datetime) -> tuple[Optional[ManualMatchEntry], dict[str, str]]:
    """フォームの値を読み取る。エラーがあれば`(None, {入力欄名: メッセージ})`を返す。

    エラーは入力欄ごとに最初の1件だけを返す(項目の直下に出すため)。
    """
    errors: dict[str, str] = {}

    def get(name: str) -> str:
        return form.get(name) or ""

    detected_at = result = rank_after = None
    try:
        detected_at = parse_detected_at(get("date"), get("hour"), get("minute"), get("second"), now)
    except ManualMatchError as exc:
        errors[exc.field] = str(exc)
    result = get("result")
    if result not in RESULTS:
        errors["result"] = "勝敗を選んでください。"
    try:
        rank_after = parse_rank(get("rank_tier"), get("rank_fraction"))
    except ManualMatchError as exc:
        errors[exc.field] = str(exc)

    sides: dict[str, list[VsSlotEntry]] = {side: [] for side in VS_SIDES}
    all_unknown = form.get("vs_all_unknown") is not None
    for side in VS_SIDES:
        for index in range(VS_SLOT_COUNT):
            if all_unknown:
                sides[side].append(VsSlotEntry())
                continue
            name = vs_field_name(side, index)
            try:
                sides[side].append(
                    parse_vs_slot(get(f"{name}_label"), get(f"{name}_value"), "vs", vs_slot_name(side, index))
                )
            except ManualMatchError as exc:
                errors.setdefault(exc.field, str(exc))
                sides[side].append(VsSlotEntry())

    if errors:
        return None, errors
    return (
        ManualMatchEntry(
            detected_at=detected_at,
            result=result,
            rank_after=rank_after,
            player_shortage=form.get("player_shortage") is not None,
            vs_mine=tuple(sides["mine"]),
            vs_opponent=tuple(sides["opponent"]),
        ),
        {},
    )


def decide_insert_position(
    detected_at: datetime, matches: Sequence[MatchRef], current_session_id: Optional[int]
) -> InsertPosition:
    """試合終了時刻から挿入位置を決める(モジュールdocstring参照)。

    `matches`は既存の全試合(記録対象外も含む)をid順に並べたもの。時系列はid順で
    表現されているため、記録時刻もid順に並んでいる前提で、前後の試合の記録時刻と
    矛盾しないかも確かめる。
    """
    following_index = next((i for i, m in enumerate(matches) if m.detected_at > detected_at), None)
    if following_index is None:
        previous = matches[-1] if matches else None
    else:
        previous = matches[following_index - 1] if following_index > 0 else None
    if previous is not None and previous.detected_at >= detected_at:
        raise ManualMatchError(
            "detected_at", f"同じ時刻の試合(id={previous.id})が既にあります。時刻を確認してください。"
        )

    if following_index is None:
        if current_session_id is None:
            raise ManualMatchError("detected_at", "配信セッションがまだ1つも無いため、追加先を決められません。")
        if previous is not None and previous.session_id == current_session_id:
            raise ManualMatchError(
                "detected_at",
                f"この時刻は今回の配信の試合(id={previous.id})より後です。"
                "追加できるのは配信の1試合目より前の試合だけです。",
            )
        return InsertPosition(session_id=current_session_id, previous=previous, following=None)

    following = matches[following_index]
    # id順と記録時刻の順が食い違っている(後ろの試合の方が時刻が前)と、挿入位置を一意に決められない
    later_but_earlier = next(
        (m for m in matches[following_index + 1 :] if m.detected_at <= detected_at), None
    )
    if later_but_earlier is not None:
        raise ManualMatchError(
            "detected_at",
            f"試合の記録時刻の並びが崩れているため(id={later_but_earlier.id})、挿入位置を決められません。",
        )
    if following.session_id is None:
        raise ManualMatchError(
            "detected_at", f"直後の試合(id={following.id})は配信セッションが記録されていないため、追加できません。"
        )
    if previous is not None and previous.session_id == following.session_id:
        raise ManualMatchError(
            "detected_at",
            f"この時刻は配信の途中(id={previous.id}とid={following.id}の間)です。"
            "追加できるのは配信の1試合目の直前だけです。",
        )
    return InsertPosition(session_id=following.session_id, previous=previous, following=following)


def check_rank_change(result: str, rank_before: float, rank_after: float) -> list[str]:
    """勝敗とランクの変化がゲーム仕様上ありえない組み合わせなら、そのメッセージを返す。

    判定は`/rank-entry`と同じ`rank_warnings.evaluate()`を使い、ルールA(勝敗と増減の
    符号)・D(2帯以上の変動)・E(勝ったのに降格/負けたのに昇格)をエラーとして扱う。
    """
    return [
        warning.message
        for warning in rank_warnings.evaluate(result=result, rank_before=rank_before, rank_after=rank_after)
        if warning.rule_code in BLOCKING_RULE_CODES
    ]


def league_change(rank_before: float, rank_after: float) -> Optional[str]:
    """帯番号(整数部)の変化から昇格/降格を返す(`db.save_manual_rank_after`と同じ判定)。"""
    tier_before = int(rank_before)
    tier_after = int(rank_after)
    if tier_after > tier_before:
        return "up"
    if tier_after < tier_before:
        return "down"
    return None
