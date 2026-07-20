# スクショ対象

> 元はObsidianのメモ(`スクショ対象.md`)。チェック済み(`[x]`)の項目は参照画像が `fixtures/screenshots/` に用意済み。未チェックの項目は今後追加予定。
>
> 「ランク増加中/減少中」(拡大表示への遷移演出の途中)の状態は、増加/減少の
> 前後の値だけが検知対象で遷移中の演出自体は現行システムの判断材料にならない
> ため、収集対象から除外した(2026-07時点の決め事)。
---

## 試合前

### チーム分け前

- [x] ロビー `00_lobby`
- [x] 潜る直前（さがすボタン待機）`01_before_start`
- [x] マッチング中 `02_matching_in_progress`

### 青チーム

- [x] 他のプレイヤー待機 `10_waiting_for_other_players_blue`
- [x] マッチング（ランク付き）`11_matching_with_rank_blue`
- [x] マッチング（ランク無し）`12_matching_without_rank_blue`

### 赤チーム

- [x] 他のプレイヤー待機 `13_waiting_for_other_players_red`
- [x] マッチング（ランク付き）`14_matching_with_rank_red`
- [x] マッチング（ランク無し）`15_matching_without_rank_red`

### 文字階級バッジの例(S/A)

`11`/`14`と同じマッチング(VS画面)だが、参加者に∞帯以外(S/A)のプレイヤーが
写っている回。Issue #40(∞以外のランク検知)対応で、`detection/vs_rank.py`の
S/A識別の検証に使用する(B/C/D/Eの参照素材はまだ無い)。

- [x] マッチング（Sランクのプレイヤーを含む）`70_rank_tier_s`
- [x] マッチング（Aランクのプレイヤーを含む）`71_rank_tier_a`

---

## 試合中

### 青チーム

- [x] 試合中（イベント無し、時間読み取りで使用？）`20_in_game_blue`
- [x] ゴール（アシスト有り）`21_goal_with_assist_blue`
- [x] ゴール（アシスト無し）`22_goal_without_assist_blue`
- [x] アシスト `23_assist_blue`
- [x] ゴール/アシスト（自分関与無し）`24_GA_without_me_blue`
- [x] 試合再開（時間読み取りで使用？）`25_resume_game_blue`

### 赤チーム

- [x] 試合中（イベント無し）`30_in_game_red`
  - 時間読み取りで使用、試合開始のものにした
- [x] ゴール（アシスト有り）`31_goal_with_assist_red`
- [x] ゴール（アシスト無し）`32_goal_without_assist_red`
- [x] アシスト `33_assist_red`
- [x] ゴール/アシスト（自分関与無し）`34_GA_without_me_red`
- [x] 試合再開（時間読み取りで使用？）`35_resume_game_red`

---

## 試合後

### 青チーム

- [x] 勝ち（ランク有り、増加前）`40_result_win_with_rank_blue`
  - [x] ランク増加後 `42_result_after_rank_increase_blue`
- [x] ==勝ち（ランク無し）`43_result_win_without_rank_blue`==
- [x] 負け（ランク有り、減少前）`44_result_lose_with_rank_blue`
  - [x] ランク減少後 `46_result_lose_after_rank_decrease_blue`
- [x] 負け（ランク無し）`47_result_lose_without_rank_blue`

### 赤チーム

- [x] ==勝ち（ランク有り、増加前）`50_result_win_with_rank_red`==
  - [x] ランク増加後 `52_result_after_rank_increase_red`
- [x] 勝ち（ランク無し）`53_result_win_without_rank_red`
- [x] ==負け（ランク有り、減少前）`54_result_lose_with_rank_red`==
  - [x] ランク減少後 `56_result_lose_after_rank_decrease_red`
- [x] 負け（ランク無し）`57_result_lose_without_rank_red`

### チーム関係無し

- [x] 延長開始 `60_start_overtime`
- [x] 延長試合中 `61_overtime_in_game`
- [x] ランクアップ `62_result_rank_up`
- [x] ランクダウン `63_result_rank_down`(ロジック要修正、Issue #TBD参照)
- [x] 引き分け(ランク無し)`64_result_draw_without_rank_blue`
  - このfixtureはランクを賭けない対戦だったため、ランクバッジが表示されず、バナー消灯後は暗転演出を挟まず直接メニュー画面に遷移している(fixtures/videos/20_draw_blue_without_rank_1-1.mp4で確認済み、Issue #26)。ランクを賭けた対戦での引き分け(バッジ表示あり)の参照素材はまだ無く、未検証(この場合もバッジ自体は動かないと予想されるため既存の検知ロジックで対応できる想定だが、実データでの確認が必要)
- [x] 試合終了バナー `65_match_end`
  - 時間切れでスコアが決定した場合、または延長戦でどちらかがゴールを決めて試合が終了した場合に表示される、画面中央やや上のミントグリーンの角丸帯(Issue #76)。色味が非常によく似た「延長戦」(60_start_overtime)・「キックオフ」バナーとの区別にはOCRによる文字確認が必要(`detection/match_end.py`参照)
