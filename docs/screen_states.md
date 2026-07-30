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

### VS画面の色シフト確認用(HDR無効化後、Issue #68)

2026-07-21にNintendo Switch側のHDR設定を無効化した後の実プレイ録画から
切り出した画像。`11`/`12`/`14`/`15`/`70`/`71`はいずれもHDR設定変更前の色
(H62-77等)で撮影されており現在の環境では再現しない。

Issue #116(2026-07-24)対応で`is_vs_screen`の閾値を実機ライブパイプライン
(FfmpegFrameReader)の実測値に更新したため、`72`/`73`(cv2.imread経由で読む
静止画)はもはや真陽性判定には使えない(cv2.imreadの色変換経路がライブ
パイプラインと異なるため。`detection/matchmaking.py`のモジュールdocstring
参照)。真陽性の検証は実測HSV値を使った合成フレームのテスト
(`tests/test_matchmaking.py`)に置き換えた。`72`/`73`自体はVS画面の見た目の
参照素材として引き続き残す。

- [x] マッチング（ランク無し、HDR無効化後）`72_matching_hdr_off_1`
- [x] マッチング（ランク無し、HDR無効化後）`73_matching_hdr_off_2`

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

### VS画面以外の色シフト確認用(HDR無効化後、Issue #147)

Issue #147対応。`72`/`73`(VS画面)に続き、それ以外の色ベース判定(勝敗バナー・ゴールバナー・試合終了バナー・リーグ昇格演出)についてもHDR無効化後の実機色で参照素材を揃えた。YouTubeアーカイブは再エンコードで色が変わる懸念があるため使わず、OBSのローカル録画(生に近いmkv、1920x1080/60fps)から直接切り出している。

- [x] ゴール(アシスト有り、赤チーム、HDR無効化後) `74_goal_with_assist_red_hdr_off`
  - 実際の描画色はピンク寄りだが、`detection/goal.py`の`RED_HUE_RANGE`・既存の命名規則(青/赤の2値)に合わせて`_red`とした(見た目の色名ではなくスロット名としての「赤」、CLAUDE.md「配色(青/赤/ピンク等)」参照)
- [x] ゴール(オウンゴール、青チーム、HDR無効化後) `75_goal_blue_owngoal_hdr_off`
  - オウンゴールのため得点者名の代わりに「オウンゴール」と表示される点に注意。バナー自体の色(`BLUE_HUE_RANGE`対象)は通常のゴールと同じ描画のはずだが、得点者名OCRのテストには使えない
- [x] 試合終了バナー(HDR無効化後、勝ち側の試合) `76_match_end_hdr_off`
- [x] 勝ち(ランク有り、赤チーム、HDR無効化後) `77_result_win_with_rank_red_hdr_off`
  - ランクバッジは**拡大表示**(ランク変動アニメーション中、バッジが一回り大きく描画される状態)を捉えたもの。当初コンパクト表示として扱われていたが、Issue #143で実際には拡大表示だったことが判明し訂正した(`GAUGE_ROI_ENLARGED`/`RANK_NUMBER_ROI_ENLARGED`が対応する)
- [x] 負け(ランク有り、青チーム、HDR無効化後) `78_result_lose_with_rank_blue_hdr_off`
  - ランクバッジは**コンパクト表示**(結果バナー確定直後)
- [x] リーグ昇格演出(HDR無効化後) `79_result_rank_up_hdr_off`
- [x] 試合終了バナー(HDR無効化後、負け側の試合、ランク無し) `80_match_end_hdr_off_2`
- [x] 負け(ランク無し、青チーム、HDR無効化後) `81_result_lose_without_rank_blue_hdr_off`
- [x] マッチング(ランク付き、4vs3の変則試合、S帯バッジ含む、HDR無効化後) `82_matching_with_rank_4v3_hdr_off`
  - 対戦相手が3人しか揃わなかった珍しいケース(4vs3)。青チーム側に∞39/S8/∞9/∞34、ピンクチーム側に∞33/∞41/S1と、S帯バッジ(Issue #40対応の識別対象)も同時に写っている。`72`/`73`はランク無しのVS画面だったため、ランク付きVS画面のHDR無効化後の参照はこれが初めて
- [x] ゴール(アシスト無し、青チーム、HDR無効化後) `83_goal_without_assist_blue_hdr_off`
  - `74`(アシスト有り)の対になる、アシスト無しの単独ゴール(オウンゴールではない通常のゴール)。Issue #153で収集。「ゴール」ラベル+得点者名が、アシスト有りの場合にアシスト側が使う位置(名前パネルの3行目・4行目)にそのまま表示される(`detection/goal.py`のモジュールdocstring参照)
- [x] 勝ち(ランク有り・コンパクト表示、青チーム、HDR無効化後) `84_result_win_with_rank_blue_hdr_off`
  - `85`と同一試合・結果バナー確定直後の瞬間(結果バナー確定直後のランクバッジ=コンパクト表示)
- [x] 勝ち(ランク有り・拡大表示、青チーム、HDR無効化後) `85_result_win_with_rank_enlarged_blue_hdr_off`
  - `84`と同一試合で、ランク変動アニメーション中(バッジが一回り大きく描画される拡大表示)を切り出したもの。`GAUGE_ROI_ENLARGED`のHDR無効化後の参照はこれが初めて(Issue #147で「まだ無い」とされていたもの)
- [x] マッチング(ランク付き、4vs4のフル編成、S帯バッジ含む、HDR無効化後) `86_matching_with_rank_4v4_hdr_off`
  - `82`(4vs3)と異なり両チームとも4人揃った通常編成。ピンクチーム側に∞0/∞9/∞30/∞38、青チーム側に∞14/∞25/∞38/S6と、こちらもS帯バッジを含む
- [x] マッチング(ランク無し、HDR無効化後) `87_matching_hdr_off_3`
  - `72`/`73`に続く3件目のランク無しマッチング画面(青チーム vs ピンクチーム)

対応する動画は`fixtures/videos/26`〜`32`(`_hdr_off`サフィックス)。いずれも試合終了バナー・結果バナー・ランク変動/リーグ昇格演出をまとめて含む区間で切り出しているため、1本の動画で複数の状態遷移を確認できる(`26_goal_red_hdr_off`/`27_goal_blue_owngoal_hdr_off`はゴール単体、`28_win_red_1-0_hdr_off`は試合終了+勝ち+ランク確定、`29_lose_blue_hdr_off`は試合終了+負け+ランク確定、`30_win_blue_league_up_hdr_off`は試合終了+勝ち+リーグ昇格演出、`31_lose_blue_without_rank_hdr_off`は試合終了+負け(ランク無し)、`32_vs_screen_with_rank_4v3_hdr_off`はマッチング待ち+VS画面(ランク付き4vs3)+キックオフ)。`tests/*_video_regression.py`等の命名規則(`{番号}_{win|lose}_{blue|red}_...`)に自動で拾われるよう、動画ファイル名も色ラベルを勝敗直後に置いている。

`83`〜`87`は上記`26`〜`32`とは別の実プレイセッション(ローカルOBS録画)から静止画として直接切り出したもので、対応する動画fixtureは無い。

- [x] 勝ち(ランク無し、青チーム、HDR無効化後) `88_result_win_without_rank_blue_hdr_off`
  - `classify_banner()`がH86.5〜87.0でWIN_HUE_RANGE=(77,86)の上限をわずかに外れ、Issue #172の既知のギャップと同じ原因でxfail扱い(`tests/test_banner.py`の`_KNOWN_HUE_SHIFT_GAPS`参照)
- [x] 引き分け(ランク無し、HDR無効化後) `89_result_draw_without_rank_hdr_off`
  - HDR無効化後で初めて収集した引き分けの参照素材。実測でS28.1〜42.1がLOSE_SAT_RANGE=(35,65)の下限をわずかに下回り、`classify_banner()`がNoneを返す既知のギャップとしてxfail扱い(`tests/test_banner.py`の`_KNOWN_DRAW_SAT_GAP`参照、閾値再較正は別issueで検討)。ランクを賭けない対戦のため`64`と同じくランクバッジは表示されない
- [x] 延長戦バナー(HDR無効化後) `90_start_overtime_hdr_off_1`・`91_start_overtime_hdr_off_2`・`92_start_overtime_hdr_off_3`
  - スタジアム・カメラアングルが異なる3件。「試合終了」バナーとの誤認識防止(`detection/match_end.py`)の回帰確認に使う。いずれも`classify_banner()`・`is_match_end_screen()`とも正しくNone/Falseを返すことを確認済み

対応する動画は`fixtures/videos/33_lose_pink_overtime_hdr_off.mp4`・`34_win_pink_overtime_hdr_off.mp4`(いずれも延長戦を経て決着する試合、ランクは38帯のままlose/winそれぞれ)。Issue #178(ランクゲージ読み取り精度)の調査で実際に誤検知が見つかった試合の元動画で、`fixtures/videos/metadata.json`に登録済み(banner_confirmed_frame_range・match_result_frame_rangeは未検証のためnull)。

`detection/team_color.py`については`82`(または`72`/`73`)のVS画面素材で代用できる。

### 追加収集(HDR無効化後、Issue #147 2026-07-30追加)

上記で優先度Low〜Mediumとして保留していたリーグ降格演出、および勝敗バナー・ゴール・試合終了バナーの追加サンプルをまとめて収集した。実プレイ数試合分の録画から静止画・動画を切り出している。

- [x] 勝ち(ランク有り・コンパクト表示、赤チーム、HDR無効化後) `93_result_win_with_rank_red_hdr_off_2`
- [x] 勝ち(ランク有り・コンパクト表示、赤チーム、HDR無効化後) `94_result_win_with_rank_red_hdr_off_3`
- [x] 勝ち(ランク有り・コンパクト表示、赤チーム、HDR無効化後、延長戦決着) `95_result_win_with_rank_red_hdr_off_4`
  - 延長戦のゴールデンゴールで決着した試合の結果バナー(対応動画`37`参照)
- [x] 勝ち(ランク無し、赤チーム、HDR無効化後) `96_result_win_without_rank_red_hdr_off`
- [x] 負け(ランク有り・コンパクト表示、赤チーム、HDR無効化後) `97_result_lose_with_rank_red_hdr_off`
- [x] 負け(ランク有り・降格ラベル付き、赤チーム、HDR無効化後) `98_result_lose_with_rank_demotion_red_hdr_off`
  - **リーグ降格演出のHDR無効化後の参照素材はこれまで無かったが、今回初めて収集できた**。CLAUDE.md記載の通り降格は全画面オーバーレイが出ず、ランクバッジ上に小さな「降格」ラベルが乗るのみ。Issue #176(降格の独立検知調査)にそのまま使える
- [x] 勝ち(ランク有り・コンパクト表示、青チーム、HDR無効化後) `99_result_win_with_rank_blue_hdr_off_2`
- [x] 勝ち(ランク有り・コンパクト表示、青チーム、HDR無効化後、帯内ゲージ微増) `100_result_win_with_rank_blue_hdr_off_3`
- [x] リーグ昇格演出(HDR無効化後、2件目) `101_result_rank_up_hdr_off_2`
- [x] ゴール(アシスト有り、青チーム、HDR無効化後) `102_goal_with_assist_blue_hdr_off`
  - 得点者プルヤ・アシストブルドッグ。**青チームの「アシスト有り」ゴールはこれが初めて**(既存`83`はアシスト無し、`75`はオウンゴールのため得点者名OCRの検証に使えなかった)。`goal_name_expectations.json`に登録済み
- [x] 試合終了バナー(「ノックアウト」併記、HDR無効化後) `103_match_end_knockout_hdr_off`
  - 通常の「試合終了」単独表示とは異なり、上段に「ノックアウト」の文字が追加された複合バナー。`detection/match_end.py`のOCR確認(`confirm_match_end_text`は部分一致のため通る想定だが未検証)・帯の位置がずれていないか個別確認が必要
- [x] ゴール(アシスト有り、赤チーム、HDR無効化後、2件目・延長戦のゴールデンゴール) `104_goal_with_assist_red_hdr_off_2`
  - 得点者ブルドッグ・アシストまいぞの。`goal_name_expectations.json`に登録済み

対応する動画は`fixtures/videos/35`〜`42`(`_hdr_off`サフィックス)。

- `35_win_red_hdr_off`・`36_win_red_hdr_off_2`: 勝ち(赤チーム、ランク有り)
- `37_win_red_overtime_goal_hdr_off`: 延長戦バナー→ゴールデンゴール(`104`)→試合終了→勝ち(`95`)までを1本で含む
- `38_win_red_without_rank_hdr_off`: 勝ち(赤チーム、ランク無し、`96`)
- `39_lose_red_goal_hdr_off`: 終盤のゴール(スコア推移から青チームの得点と確認済み。ただし「ゴール!」の色付きフラッシュバナーが始まる直前から切り出されており、名前パネルのみで色帯自体は写っていない)→試合終了→負け(`97`)
- `40_lose_red_demotion_hdr_off`: 試合終了→負け+降格ラベル(`98`)
- `41_win_blue_goal_knockout_hdr_off`: ゴール(`102`)→「ノックアウト/試合終了」複合バナー(`103`)→勝ち(`99`)
- `42_win_blue_league_up_hdr_off_2`: 勝ち→リーグ昇格演出(`101`)

`fixtures/videos/metadata.json`への登録(`expected_result`等)は今回見送った。タイミング系の期待値と同様、実装の出力を転記せず実際に動画を最後まで見て確定させる必要があるため(既存の運用方針)、必要になった時点で確認の上登録すること。
