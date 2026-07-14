---
title: スクショ対象
date: 2026-07-10
type:
tags:
  - hobby
---

> 元はObsidianのメモ(`スクショ対象.md`)。チェック済み(`[x]`)の項目は参照画像が `fixtures/screenshots/` に用意済み。未チェックの項目は今後追加予定。

# スクショ対象
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
- [ ] マッチング（ランク無し）`15_matching_without_rank_red`
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
- [ ] 勝ち（ランク有り、増加前）`40_result_win_with_rank_blue`
	- [ ] ランク増加中 `41_result_in_rank_increase_blue`
	- [ ] ランク増加後 `42_result_after_rank_increase_blue`
- [x] ==勝ち（ランク無し）`43_result_win_without_rank_blue`==
- [x] 負け（ランク有り、減少前）`44_result_lose_with_rank_blue`
	- [x] ランク減少中 `45_result_lose_in_rank_decrease_blue`
	- [x] ランク減少後 `46_result_lose_after_rank_decrease_blue`
- [ ] 負け（ランク無し）`47_result_lose_without_rank_blue`

### 赤チーム
- [x] ==勝ち（ランク有り、増加前）`50_result_win_with_rank_red`==
	- [x] ランク増加中 `51_result_in_rank_increase_red`
	- [x] ランク増加後 `52_result_after_rank_increase_red`
- [ ] 勝ち（ランク無し）`53_result_win_without_rank_red`
- [x] ==負け（ランク有り、減少前）`54_result_lose_with_rank_red`==
	- [x] ランク減少中 `55_result_lose_in_rank_decrease_red`
	- [x] ランク減少後 `56_result_lose_after_rank_decrease_red`
- [ ] 負け（ランク無し）`57_result_lose_without_rank_red`

### チーム関係無し
- [x] 延長開始 `60_start_overtime`
- [ ] 延長試合中 `61_overtime_in_game`
- [ ] ランクアップ `62_result_rank_up`
- [ ] ランクダウン `63_result_rank_down`
