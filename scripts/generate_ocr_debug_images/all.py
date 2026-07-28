"""banner.py / goal.py / league_change.py / match_end.py / rank_ocr.py /
vs_rank.py をまとめて実行する(fixtures/ocr_debug/配下の全モジュール分を
一括で最新化したいときに使う)。

個別のROIを試したいときは、対象のスクリプトを直接編集して単体実行する方が
速い(このスクリプトはROI変数を持たない)。
"""

import banner
import goal
import league_change
import match_end
import rank_ocr
import vs_rank


def main() -> None:
    rank_ocr.main()
    vs_rank.main()
    goal.main()
    match_end.main()
    banner.main()
    league_change.main()


if __name__ == "__main__":
    main()
