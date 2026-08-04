"""試合の状態遷移を管理する状態機械。

CLAUDE.md記載の「試合後の状態遷移」(結果バナー表示→ランク変動アニメーション→
ランク確定→暗転→マッチング画面)を、banner/rank_ocr/motion/league_changeの
各検知結果をつないで管理する。フレームを1枚ずつ process_frame() に渡すと、
試合の記録が完了した瞬間だけ MatchResult を返す。

「暗転」を明示的な輝度閾値で検知するのではなく、結果バナーが一定時間
確実に消えたこと(banner=Noneがbanner_absence_confirm_frames回連続)を
もって次の試合への再武装(WATCHING状態への復帰)とみなす。暗転〜マッチング
画面のどこかで必ずバナーが消えるため、この方が輝度閾値を新たに調整するより
頑健(検証済みの banner判定・デバウンスの仕組みをそのまま再利用できる)。

banner判定は単体だと一瞬誤検知しうるため(detection.banner参照)、ここでも
banner_confirm_frames 回連続した判定のみを採用する。デフォルト値は30fps想定で
約2秒(Issue #67対応、後述)。60fps等より高フレームレートで使う場合は、呼び出し側で
fpsに応じて調整すること。

Issue #67: 実プレイ配信のアーカイブ映像で、試合中(ゴール演出とは無関係な通常プレイ中)に
画面上部(BANNER_ROI)へスタジアムの背景(建造物等)が写り込み、classify_bannerが
誤って"lose"を1.3秒程度連続して返し、デバウンス(当時1秒)をすり抜けて結果バナーの
誤検知(試合が2つに分割される)が発生した。当初「ゴール演出中はバナー判定を止める」
というステートベースの回避策を検討したが、実際にこの誤検知が起きた区間は
is_goal_event()が終始Falseの通常プレイ中であり、この案は実データに対して無効だった。
また、色・形状ベースの追加判定条件(HUD要素の有無・複数ROIでの整合性チェック等)も
検討したが、本物のバナー自体が表示直後にアニメーションで縮小・変形するため、
確定に必要な連続フレーム区間の途中で条件を満たさなくなり、いずれも本物の確定を
壊してしまうことが実データ検証で判明した。この種の誤検知パターンの参照サンプルが
現時点で1件のみで、閾値を「範囲+マージン」で決められるだけのデータが無いため、
根本的な検知改善は今後の課題とし、今回は対症療法としてbanner_confirm_framesを
1秒から2秒に延長した(誤検知は1.3秒程度しか持続せず、本物のバナーは数秒以上
表示され続けるため、2秒あれば今回のサンプルは確実に防げる。検知遅延が数秒増える
が、バックグラウンドでの記録用途のため実用上の影響はない)。回帰テストは
fixtures/videos/21_goal_event_false_positive_win_blue_4-3.mp4
(tests/test_match_state.pyのtest_match_state_machine_matches_expected_metadata、
fixtures/videos/metadata.json参照)。

Issue #76: Issue #67の2秒デバウンスは対症療法であり検知が遅くなる副作用がある。
これを改善するため、試合の本当の終了時点にのみ表示される「試合終了」バナー
(detection.match_end参照)を補助信号として使う。WATCHING中に
is_match_end_screen()を毎フレーム軽量にチェックし、match_end_confirm_frames回
連続したタイミングで1回だけconfirm_match_end_text()を呼んでOCRで文字を確認する
(「試合終了」と色味が酷似する「延長戦」「キックオフ」バナーとの誤認識を防ぐため。
detection.match_end参照)。「試合終了」を確認できていれば、_watch_for_banner()の
確定に必要なbanner_streakの閾値をbanner_confirm_frames_after_match_end
(短い、デフォルトはIssue #67修正前と同じ1秒相当)に切り替える。確認できていない
場合は通常どおりbanner_confirm_frames(長い、2秒相当)のままとする。

この設計を「VS画面〜試合終了検知までの間はバナー判定自体を行わない」という
完全なゲート方式にしなかったのは、「試合終了」検知自体を見逃す実データケースが
あったため(実測: fixtures/videos/21_goal_event_false_positive_win_blue_4-3.mp4は
「試合終了」が本来映るはずの区間を含んでいない)。完全ゲート方式だと見逃した
試合が丸ごと記録から欠落してしまうが、本方式(見ていれば高速、見ていなくても
既存の安全側デバウンスにフォールバック)であれば、見逃しても速度面の恩恵を
逃すだけで正しさは損なわれない。VS画面検知(Issue #39、下記)を「見逃しても
既存フローに影響しない任意のエンリッチ」として扱っている設計方針と同じ考え方。

Issue #186: is_goal_event()(「ゴール!」バナーの色ベース候補判定)も、Issue #76の
「試合終了」と同じ理由で青空・スタジアム天蓋の映り込みに誤反応することが
広域監査で判明した(本物の青ゴールバナーとHSVが実測で重複しており、色閾値の
みでの安全な分離は不可)。同じ2段構成の考え方で、_check_for_goal()は
goal_confirm_frames回連続したタイミングで1回だけconfirm_goal_text()を呼び、
得点者名パネルのラベル文字(「ゴール」「アシスト」「オウンゴール」、固定語彙で
OCRの信頼性が高い)が実際に読み取れた場合のみゴールとして確定する
(detection.goalのモジュールdocstring参照)。確認できなかった場合はその
ストリークでは記録せず、is_goal_event()が一旦Falseに戻ってストリークがリセット
されるまで再確認しない(「試合終了」同様、重いOCRを毎フレーム呼ばないための
デバウンス+1回確認の設計)。

Issue #71: 実プレイでの動作確認をしやすくするため、試合のライフサイクルの節目
(セッション内でn試合目か、を含む)をINFOログとして出す。カウンタ(_session_match_no)は
「試合開始」ログ(_check_for_vs_screen、VS画面確定時)でのみ増加させ、「試合終了」
(_check_for_match_end、confirm_match_end_text確認時)・「結果」(_watch_for_banner、
バナー確定時、この時点ではランクは未確定なのでrank_beforeのみ含める)の各ログは
直前に増加させた番号をそのまま参照する。VS画面・「試合終了」バナーはいずれも
見逃しうる(実データで確認済み、上記参照)ため、対応するログ自体が出ないことが
あるが、番号がズレるより見逃し時にログが欠落する方を許容する方針とした
(ユーザーとすり合わせ済み)。

ランク確定判定(TRACKING_RANK)は、ピクセル差分が一旦安定しても「リーグ昇格」の
全画面演出がそのあとに続く場合があることが実データで判明している
(fixtures/videos/01_win_blue_2-1.mp4)。安定を検知した直後にすぐ
確定させず、league_change_grace_frames分だけ様子を見て、その間に演出が現れたら
演出が終わるまで待ち、再度安定するのを待ってから確定する(detection.league_change
参照)。なお、この全画面演出が出るのは**昇格時のみ**。降格時は全画面演出が出ず
ランクバッジ上に小さな「降格」ラベルが乗るだけでバッジ自体は隠れない
(fixtures/videos/10_RankDown_red.mp4で確認済み)ため、is_league_change_screen()
はIN_LEAGUE_CHANGE状態には遷移しない。この場合でも下記のGRACE中のバナー消灯
フォールバック・rank_recheck機構により正しく確定できる。

さらに、ゲージがフレーム間差分の閾値を下回る速度でごく緩やかに増減し
続けるケースが実データで確認されている(fixtures/videos/00_lose_red_2-3.mp4,
03_lose_blue_2-3.mp4)。StabilityMonitorは直前フレームとの差分しか見ないため、
1フレームごとの変化量が小さいまま数十フレームかけて値が動き続けても
「安定」の判定が崩れず、GRACE突入直後に読んだ値が古いまま確定されてしまう
(例: 00は真の最終値40.43より先に一時的な40.77を確定、03は降格後の
39台への遷移を見逃す)。これに対処するため、GRACE中も帯番号(数値OCR、重い
処理)はrank_recheck_interval_framesおきに読み直して古くなっていないか確認する。

Issue #178: ゲージの塗りつぶし(小数部)側は、上記の間引き読み直しだけでは
不十分なケースが実データ(本番運用中に記録されたmatches.id=19/20の元動画)で
見つかった。`--video`実行時の実時間再生(-re)とFfmpegFrameReaderの「処理が
追いつかない間は古いフレームを破棄する」設計の組み合わせにより、実際に
処理されるフレームの間隔が不規則になる。その間隔がたまたま「差分が小さく
見える」タイミングに重なると、StabilityMonitorがまだアニメーション途中
(ゲージがゆっくり動いている最中)を「安定」と誤判定し、収束前の値を
スナップショット的に確定してしまう。ゲージの塗りつぶし(HSVベースの軽量な
色判定)自体は数値OCRと違って毎フレーム読んでも負荷が軽いため、GRACE中は
スナップショットではなく毎フレーム最新値で上書きし続け、確定時にはその
時点の最新値を使う方式に変更した(_current_grace_rank参照)。帯番号側は
従来通りの間引き読み直しのままでよい(数値OCRは重く、かつ帯自体は基本的に
1試合で1回しか変わらないため)。

Issue #178で追加した、GRACE中に「バナー消灯+直近rank_recheck_interval_frames分
ゲージが変化していない」ことを合図に早期確定するパスは、Issue #209で新たな
不具合を引き起こしていたことが判明した。リーグ**昇格**が起きる試合では、
昇格演出(is_league_change_screen)が実際に始まる前に、旧い帯のゲージが上限に
到達して一時的に本当に動かなくなる「踊り場」が生じる。この踊り場のタイミングで
結果バナーのテキストがたまたま一瞬(実データで最大49フレーム、0.8秒程度)
消えると、上記の早期確定条件を満たしてしまい、昇格演出が始まるより前に
確定してしまう(fixtures/videos/30・42番、いずれも実際に昇格が起きる試合で
100%再現)。この時点では帯番号自体はまだ変化していない(delta=0)ため
_is_tier_change_plausible()も無条件に許容してしまい、警告も再スキャンも
一切トリガーされない。結果として`league_changed`が`None`のまま、`rank_after`
だけが帯の上限にかなり近い値(実測0.993〜1.0)として記録される、という
外からは気付きにくい形の見逃しになる。

対策として、_latest_gauge_fillがLEAGUE_CHANGE_IMMINENT_FILL_THRESHOLD以上の
場合はこの早期確定パスを使わないようにした。この場合は通常どおり
league_change_grace_frames(既定5秒相当)の満了まで待つことになるが、
実データ(30・42番)ではバナー確定から実際の昇格演出開始まで3.5秒前後だった
ため、5秒の猶予期間内に収まる。昇格演出が始まれば_track_rank()冒頭の
is_league_change_screen()分岐が先に捕捉するため、正しく昇格として扱われる。
演出後にバッジが安定して読み取れることは実データで確認済み(30番は演出後
約60フレーム、42番は約30フレームの安定した静止区間があり、いずれも既存の
StabilityMonitorの安定待ち(30fps換算15フレーム相当)で問題なく間に合う)。

ただし上記のleague_change_grace_frames満了待ちには依然として上限時間が
あるため、理論上はそれより長く昇格演出の開始が遅れた場合(例: 何らかの理由で
結果画面のまま長時間状態が変化しない)、同じ形の見逃しが再現しうる。この
残存リスクに対する最終的な安全装置として、CLAUDE.md記載の「4. 暗転」
(ランク確定〜昇格演出を含む一連の演出が完全に終わった直後、マッチング画面に
戻る前に必ず一度全画面が真っ黒になる区間、detection.motion.is_full_blackout
参照)を検知したら、grace_counter・near_tier_cap・バナー消灯確認の状態に
一切関わらず直ちに確定するようにした(Issue #209)。暗転はランク確定と
無関係なタイミング(マッチング開始直後・対戦相手が集まらずゲーム再起動する際等)
でも起こりうるが、この判定はGRACE以降(_grace_candidate_rank_tierが一度でも
読み取れた後)でのみ使うため、他のタイミングでの暗転が誤って確定をトリガー
することはない。fixtures/videos全24本を実測し、結果バナーを含む全ての
試合系クリップで暗転区間が輝度平均0.40〜0.43・標準偏差8.0〜8.2に収まり、
それ以外の区間の最も暗いフレームでも輝度平均30以上だったことを確認済み
(detection.motion.is_full_blackoutのモジュールdocstring参照)。

ゴール(得点・アシスト)はWATCHING中(試合結果バナーを待っている=まさに
プレイ中の期間)にのみ起こりうるため、_watch_for_banner()と並行して
毎フレームチェックする。検知したゴールは試合単位でメモリ上にバッファし
(_pending_goals)、_finalize()でMatchResult.goalsとして払い出す。
得点者が許可リスト(config.is_allowed_player)に無い場合に記録すら
しないという方針は、この状態機械ではなく永続化層(database.db.save_goal)
の責務とする(検知層はポリシーを持たず、見えたものをそのまま報告する)。

VS画面(マッチング完了、Issue #39)もWATCHING中にのみ起こりうる(結果バナーより
前、試合開始時点の一瞬だけ表示される)ため、ゴールと同様_watch_for_banner()と
並行してチェックする。banner判定と同じデバウンス(vs_screen_confirm_frames回
連続)で確定させ、確定した瞬間に1回だけdetection.vs_rank.read_vs_screen_ranks()
を呼び出してMatchResult.vs_mine_ranks/vs_opponent_ranksとして払い出す
(detection.vs_rank側のOCRは重い処理のため、CLAUDE.mdのサンプリング戦略どおり
毎フレームは呼ばない)。VS画面を見逃した試合ではどちらも空リストのままになる
(Issue #39で「VS画面検知は任意のエンリッチとし、見逃しても既存の結果バナー
起点フローは従来通り動作させる」と定めたとおり、必須の前提にしない)。

MatchResult/GoalEventのdetected_atはJST(timeutil.now_jst参照)で記録する。
また、結果バナー確定時・試合終了確定時にランクバッジのOCRが失敗した場合
(バッジがそもそも表示されていない場合と見た目上区別できない)は、後から
記録結果だけを見ても原因が分からないためログに残す。

Issue #83: OBSシーン自動切り替え(obs_control.ObsSceneController)のトリガーとして
`in_match`プロパティを公開する。VS画面確定(_check_for_vs_screen)でTrueになり、
_finalize()(ランク確定・league_changed判定を含む試合結果の確定)でFalseに戻る。
「試合終了検知(match_end)の時点で即座に試合間シーンへ切り替える」案も検討したが、
ランクを賭けた試合ではランク変動アニメーションもフルスクリーンの試合画面側で
見せたいというユーザーの意向により不採用とし、_finalize()完了(ランク確定後)を
唯一の切り替えタイミングとした。VS画面を見逃した試合ではin_matchがTrueにならず
試合中シーンへ切り替わらないが、Issue #39の「VS画面検知は任意のエンリッチ」という
既存方針と同じ考え方で許容する(見逃しても以降のフローに影響しない)。

Issue #145: 対戦相手ランク比較ウィジェット(web/server.py)は、試合結果確定
(MatchResult、試合終了後にまとめて払い出される)を待たず、VS画面を確定した
瞬間にDBへ反映して即座に表示を更新したい。process_frame()の戻り値
(MatchResultは試合終了時の1回だけ)とは別に、`pop_vs_screen_event()`で
「VS画面を確定した直後の1フレームだけ」読み取り結果を取得できるようにする
(in_matchのような常時参照可能なプロパティではなく、process_frame()と同じ
「取得したら消費される」設計。main.py側がprocess_frame()呼び出しのたびに
ポーリングし、Noneでなければその場でDBへ即時反映する)。

Issue #190: 実プレイ中(ゴール演出とは無関係な通常プレイ中)の背景誤検知が
banner_confirm_frames(2秒デバウンス)を突破し、OBSシーンが誤って試合中→
試合間(ワイプ)へ切り替わってしまう事象が実配信で確認された。特にランクを
賭けない試合(rank_before=Noneのため_is_tier_change_plausible等の数値ベースの
安全装置が一切効かない)は、StabilityMonitorの「安定」判定さえ誤検知フレームで
たまたま満たされれば、あとはbanner_confirm_framesの2秒デバウンスだけが最後の
砦になる。配信者体験として「マッチング待機中に誤って試合中シーンのままになる」
より「実プレイ中に誤ってワイプへ切り替わる」方がはるかに困るという優先順位が
示されたため、_check_for_match_end()で「試合終了」バナーのOCR確認
(confirm_match_end_text)ができた試合に限り、_finalize()でin_matchをFalseに
戻す(OBSシーン切替を実行する)ことにした。確認できなかった試合は、
MatchResultの記録自体(勝敗・ランク)は従来どおり行うが、in_matchはTrueの
ままにする(OBSシーン切替は見送り、試合中シーンに留まる)。既存の
banner_confirm_frames_after_match_end(デバウンス短縮)用途とは別に
_match_end_confirmed_this_matchで確認結果を_finalize()まで持ち越す
(_match_end_seenは短縮用のフラグのままbanner確定時にリセットされるため、
そのままでは_finalize()到達時点で常にFalseになってしまう)。

見逃した場合、in_matchはその試合の終了時点ではFalseに戻らず、次の試合の
VS画面確定(既にTrueなので実質no-op)を経て、次にmatch_endを確認できた
試合の_finalize()で初めてFalseに戻る。「見逃した試合の間は試合中シーンに
居座り続ける」形になるが、これはユーザーが許容すると明言した失敗方向であり、
DB記録自体は毎試合従来どおり行われるため実害は無い(モジュールトップの
Issue #76と同じ「見逃しても既存フローの正しさは損なわれない」設計)。

あわせて、match_end_confirm_frames(色候補判定→OCR確認までのデバウンス)を
1フレームに短縮した。このデバウンスは実質「誤検知を防ぐ安全マージン」としては
機能しておらず、実際に真偽を決めているのはOCRの文字一致(confirm_match_end_text)
そのものである(色条件を満たした最初のフレームで即OCRを呼んでも、「延長戦」
「キックオフ」等の誤った文字列であればOCR側で弾かれるため誤検知には
つながらない)。むしろ唯一実測されている不具合(29_lose_blue_hdr_off.mp4の
frame 958、表示が消える直前の縮小アニメーションでOCRが失敗したケース)は
「確認が遅すぎて表示終了直前の不安定なフレームに当たった」方向のリスクのため、
デバウンスを縮めて可能な限り早いフレームで1回きりのOCRを実行する方が安全。

Issue #176: 降格(帯番号-1)は、昇格(is_league_change_screen()の全画面
オーバーレイ)と違って独立した確認手段が無く、_infer_tier_from_gauge_continuity()の
「負けているのにゲージ小数部が閾値を超えて増えて見える」という間接的な
推測に頼っていた。調査の結果、降格時にランクバッジ上へ乗る「降格」ラベル
(白背景の吹き出し)が、Issue #73で断念したS/A帯バッジのOCRとは異なり
形状(輝度)・OCRいずれの手法でも安定して検知できることが分かったため
(detection/league_change.pyのモジュールdocstring参照)、
is_demotion_label_candidate()/confirm_demotion_label_text()を追加した。
match_end/goalと同じ2段構成(色/形状の軽量な候補判定→デバウンス確定時に
1回だけOCRで確認)を、TRACKING_RANK中(_track_rank())で常時チェックする形で
組み込み、確認できれば_demotion_confirmed_this_matchに保持する。
_infer_tier_from_gauge_continuity()では、この独立信号が得られていれば
ゲージ小数部の閾値判定より優先して降格と確定させ、得られていない場合は
従来どおりの間接的な推測にフォールバックする(見逃しても既存の正しさは
損なわれない、という他の2段構成の信号と同じ設計)。

Issue #202: 上記の実装直後、降格ラベルを確認できていても帯番号OCRが
「変化なし」(delta=0)を返した場合には独立信号が一切参照されず、降格が
記録から漏れる穴が見つかった。_is_tier_change_plausible()はdelta=0を
無条件に許容していたため、_infer_tier_from_gauge_continuity()への
フォールバック自体が発生しなかったことが原因。負け試合かつ
_demotion_confirmed_this_matchがTrueの場合はdelta=0も不自然とみなす
よう修正し、他の帯番号急変ケースと同じ再スキャン経路に合流させることで、
最終的にtier_before-1として記録できるようにした。降格の受理条件自体
(delta=-1は`_pending_result == "lose"`のみで足りる、既存の緩い条件)は
変更していない。同様の穴は昇格側(is_league_change_screenを確認できて
いるのに帯番号OCRが変化無しに化けるケース)にも対称的に存在するが、
今回のスコープには含めない(ユーザーと合意の上、必要になれば別issueで
対応する)。

Issue #189: VS画面確定〜OBSシーン切替(in_match=True)までが実配信で10〜17秒
遅れる不具合を調査したところ、色閾値のズレ(Issue #68/#116で一度あった前例)
ではなく、`_check_for_vs_screen()`がVS画面確定のたびに同期的に呼んでいた
`read_vs_screen_ranks()`(両チーム最大4人×アイコン判定+数値OCRで最大16回の
PaddleOCR推論)が原因と判明した。実測でCPU上9〜16秒かかり、この間
`process_frame()`全体がブロックされるため、次のフレームが読めないだけでなく、
`main.py`の実行ループが`machine.in_match`の変化(OBSシーン切替のトリガー)に
気付くタイミングもOCR完了まで遅延していた。単に`self._in_match = True`の
代入位置をOCR呼び出しより前に移動するだけでは解決しない(`process_frame()`
自体が同期呼び出しである以上、関数全体がOCR完了まで戻らないため)。

対策として、VS画面確定を検知した瞬間(`_vs_screen_confirm_frames`のデバウンス
成立時)に`self._in_match = True`・`self._session_match_no`のインクリメント・
「試合開始」ログを即座に行い、`read_vs_screen_ranks()`/`read_team_colors()`は
`_run_vs_ocr()`としてバックグラウンドスレッドに切り出した。OCR完了後に
`_pending_vs_mine_ranks`等のpendingフィールドと`VsScreenEvent`
(`pop_vs_screen_event()`、Issue #145)をスレッド側から書き込む。この試合が
完全に終わる(`_finalize()`)までは`_vs_recorded_this_match`がTrueのままなので、
同じ試合中に次のVS画面OCRが重ねて走ることはない。`_finalize()`はpendingフィールドを
`MatchResult`に積む前にこのスレッドの完了を`join()`で待つ(通常はOCR自体が
最大16秒・試合は数分続くため待たされることはないが、念のための安全策)。
`_vs_screen_event`はpoll側(`pop_vs_screen_event()`、main.pyのループから毎フレーム
呼ばれる)と書き込み側(バックグラウンドスレッド)が並行アクセスするため、
`_vs_screen_event_lock`で保護する。
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from logging import DEBUG, getLogger
from typing import NamedTuple, Optional

import numpy as np

from nss_tracker.config import get_goal_record_mode, is_allowed_player
from nss_tracker.detection.banner import BannerResult, classify_banner
from nss_tracker.detection.goal import (
    confirm_goal_text,
    is_goal_event,
    is_own_goal_event,
    read_assist_name,
    read_scorer_name,
)
from nss_tracker.detection.league_change import (
    confirm_demotion_label_text,
    is_demotion_label_candidate,
    is_league_change_screen,
)
from nss_tracker.detection.match_end import confirm_match_end_text, is_match_end_screen
from nss_tracker.detection.matchmaking import is_vs_screen, read_letterbox_brightness, read_vs_roi_hsv
from nss_tracker.detection.motion import StabilityMonitor, is_full_blackout
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    RANK_NUMBER_ROI_COMPACT,
    RANK_NUMBER_ROI_ENLARGED,
    RANK_ROI,
    read_precise_rank,
    read_rank,
    read_rank_gauge_fill,
)
from nss_tracker.detection.team_color import read_team_colors
from nss_tracker.detection.vs_rank import SlotRank, read_vs_screen_ranks
from nss_tracker.detection_config import get_detection_value
from nss_tracker.timeutil import now_jst

logger = getLogger("nss_tracker.state")

# Issue #71: 勝敗結果ログ用の表示文言
_BANNER_RESULT_LABELS = {"win": "勝ち", "lose": "負け", "draw": "引き分け"}

# Issue #67: 通常プレイ中の背景誤検知(1.3秒程度持続)を確実に防ぐため、
# 他のconfirm系(1秒相当)より長い2秒相当のデフォルト値にしている
DEFAULT_BANNER_CONFIRM_FRAMES = 60
# Issue #76: 「試合終了」バナーを確認できている場合のbanner_confirm_frames。
# Issue #67修正前のデフォルト(1秒相当)と同じ値に戻す(本当の試合終了直前だと
# 分かっているため、通常プレイ中の背景誤検知を心配する必要が無い)
DEFAULT_BANNER_CONFIRM_FRAMES_AFTER_MATCH_END = 30
DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES = 30
DEFAULT_GOAL_CONFIRM_FRAMES = 30
DEFAULT_VS_SCREEN_CONFIRM_FRAMES = 30
# Issue #176: 降格ラベルは実測で2秒以上安定して表示され続けるため(detection/
# league_change.pyのモジュールdocstring参照)、goal/vs_screenと同じ1秒相当で良い
DEFAULT_DEMOTION_LABEL_CONFIRM_FRAMES = 30
# Issue #190: このデバウンスは誤検知を防ぐ安全マージンとしては機能しておらず
# (実際の真偽はOCR文字一致confirm_match_end_textが決める)、「試合終了」バナーは
# 実データで最短7フレーム程度(60fps)しか綺麗に表示されないケースがあったため、
# 色候補判定を満たした最初のフレームで即OCR確認する(モジュールdocstring参照)
DEFAULT_MATCH_END_CONFIRM_FRAMES = 1
# 実測(fixtures/videos/01_win_blue_2-1.mp4, 60fps):
# ランク数値が一旦静止してから昇格演出が始まるまで約270フレーム(4.5秒)の間があった
DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES = 150
# GRACE中にゲージの緩やかな変化を見逃さないよう再読み取りする間隔(フレーム数)
DEFAULT_RANK_RECHECK_INTERVAL_FRAMES = 15
# 再読み取りで「値が変わった」とみなす閾値。ゲージ読み取り自体の測定誤差
# (tests/test_rank_ocr.pyでabs=0.02を許容)より大きく取り、ノイズで
# 猶予期間を無駄に延長し続けないようにする
# (config/detection.tomlの[match_state]で上書き可能)
RANK_RECHECK_CHANGE_TOLERANCE = get_detection_value("match_state", "RANK_RECHECK_CHANGE_TOLERANCE", 0.05)

# Issue #136: 試合前後で帯番号(整数)が2以上急変した場合の再スキャンまでの
# 待機フレーム数(30fps想定)。同一フレームへの再OCRは同じ誤読を繰り返すだけ
# のため、数フレーム後の別フレームで読み直す
DEFAULT_RANK_TIER_RESCAN_WAIT_FRAMES = 5

# Issue #136: 再スキャンしても帯番号が不自然なまま(1帯を超える変化、または
# 昇格演出未確認の+1、または勝敗と矛盾する向きの変化)だった場合、ゲージ小数部
# (HSVベースの独立信号)の連続性で判断し直す。「勝ったら降格しない/負けたら
# 昇格しない」というゲーム仕様(ユーザー確認済み)を前提に、勝敗と矛盾する
# 向きにこの割合を超えて動いて見える場合にのみ1帯またいだとみなす
RANK_TIER_WRAP_MIN_MAGNITUDE = get_detection_value("match_state", "RANK_TIER_WRAP_MIN_MAGNITUDE", 0.5)

# Issue #209: ゲージがこの値以上のとき、昇格演出が近い(帯の上限に到達し一時的に
# 停止している)可能性があるとみなし、バナー消灯による早期確定パス(Issue #178)を
# 使わない。実データ(fixtures/videos/30・42番)では、早期確定に誤って捕まった
# 時点のゲージ小数部が0.993〜1.0だったため、十分マージンを取った値にしている
LEAGUE_CHANGE_IMMINENT_FILL_THRESHOLD = get_detection_value("match_state", "LEAGUE_CHANGE_IMMINENT_FILL_THRESHOLD", 0.95)


class _State(Enum):
    WATCHING = auto()
    TRACKING_RANK = auto()
    COOLDOWN = auto()

    @property
    def label(self) -> str:
        return self.name.lower()


class _RankPhase(Enum):
    WAITING_STABLE = auto()
    GRACE = auto()
    IN_LEAGUE_CHANGE = auto()
    RESCAN_WAIT = auto()


@dataclass
class GoalEvent:
    scorer_name: Optional[str]
    assist_name: Optional[str]
    detected_at: datetime
    # Issue #217: オウンゴールかどうか(検知層が見たものをそのまま報告するのみで、
    # GOAL_RECORD_MODEに応じた記録可否の判断はdatabase.db.save_goal側の責務)
    is_own_goal: bool = False


class VsScreenEvent(NamedTuple):
    """VS画面を確定した直後の1フレームだけ`pop_vs_screen_event()`が返す読み取り結果(Issue #145)。

    フィールドの意味はMatchResultの同名フィールドと同じ。
    """

    mine_ranks: list[SlotRank]
    opponent_ranks: list[SlotRank]
    mine_team_color: Optional[str]
    opponent_team_color: Optional[str]


@dataclass
class MatchResult:
    result: BannerResult
    rank_before: Optional[float]
    rank_after: Optional[float]
    league_changed: Optional[str]  # "up" / "down" / None
    detected_at: datetime
    goals: list[GoalEvent] = field(default_factory=list)
    # VS画面(マッチング完了)を見逃した試合ではどちらも空リストのまま
    # (Issue #39: VS画面検知は任意のエンリッチであり必須の前提にしない)
    vs_mine_ranks: list[SlotRank] = field(default_factory=list)
    vs_opponent_ranks: list[SlotRank] = field(default_factory=list)
    # チームカラー(Issue #113)。vs_mine_ranks等と同じくVS画面を見逃した試合では
    # 両方Noneのまま(必須の前提にしない)
    mine_team_color: Optional[str] = None
    opponent_team_color: Optional[str] = None


class MatchStateMachine:
    """フレームを1枚ずつ渡して試合結果を検知する状態機械。"""

    def __init__(
        self,
        rank_roi: tuple[int, int, int, int] = RANK_ROI,
        banner_confirm_frames: int = DEFAULT_BANNER_CONFIRM_FRAMES,
        banner_confirm_frames_after_match_end: int = DEFAULT_BANNER_CONFIRM_FRAMES_AFTER_MATCH_END,
        banner_absence_confirm_frames: int = DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES,
        league_change_grace_frames: int = DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES,
        goal_confirm_frames: int = DEFAULT_GOAL_CONFIRM_FRAMES,
        rank_recheck_interval_frames: int = DEFAULT_RANK_RECHECK_INTERVAL_FRAMES,
        vs_screen_confirm_frames: int = DEFAULT_VS_SCREEN_CONFIRM_FRAMES,
        match_end_confirm_frames: int = DEFAULT_MATCH_END_CONFIRM_FRAMES,
        rank_tier_rescan_wait_frames: int = DEFAULT_RANK_TIER_RESCAN_WAIT_FRAMES,
        demotion_label_confirm_frames: int = DEFAULT_DEMOTION_LABEL_CONFIRM_FRAMES,
        rank_stability_monitor: Optional[StabilityMonitor] = None,
    ) -> None:
        self._banner_confirm_frames = banner_confirm_frames
        self._banner_confirm_frames_after_match_end = banner_confirm_frames_after_match_end
        self._banner_absence_confirm_frames = banner_absence_confirm_frames
        self._league_change_grace_frames = league_change_grace_frames
        self._goal_confirm_frames = goal_confirm_frames
        self._rank_recheck_interval_frames = rank_recheck_interval_frames
        self._vs_screen_confirm_frames = vs_screen_confirm_frames
        self._match_end_confirm_frames = match_end_confirm_frames
        self._rank_tier_rescan_wait_frames = rank_tier_rescan_wait_frames
        self._demotion_label_confirm_frames = demotion_label_confirm_frames
        self._rank_monitor = rank_stability_monitor or StabilityMonitor(roi=rank_roi)

        self._state = _State.WATCHING
        self._rank_phase = _RankPhase.WAITING_STABLE
        self._grace_counter = 0
        self._banner_candidate: BannerResult = None
        self._banner_streak = 0
        self._absence_streak = 0
        self._pending_result: BannerResult = None
        # 帯番号(int)はleague_changed判定に、小数のランク値(float)はMatchResultの
        # 報告値に使う。ゲージの溜まり具合による僅かな変動をリーグ変動と
        # 誤検知しないよう、判定には必ず帯番号(整数)側を使うこと
        self._pending_rank_before_tier: Optional[int] = None
        self._pending_rank_before: Optional[float] = None
        self._grace_candidate_rank_tier: Optional[int] = None
        # Issue #178: ゲージの塗りつぶし(小数部)の最新値。GRACE中は毎フレーム
        # 上書きし続け、確定時にはスナップショットではなくこの最新値を使う
        self._latest_gauge_fill: Optional[float] = None
        # Issue #136: 昇格演出(is_league_change_screen)がこの試合中に一度でも
        # 観測されたか。帯番号の急変を検証する際、昇格側はこの独立信号で
        # 確認できていない限り認めない
        self._promotion_confirmed_this_match = False
        # Issue #176: 降格ラベル(is_demotion_label_candidate/confirm_demotion_label_text)を
        # この試合中に確認できたか。_infer_tier_from_gauge_continuity()で
        # ゲージ小数部の間接推測より優先して使う独立信号
        self._demotion_confirmed_this_match = False
        self._demotion_label_streak = 0
        self._demotion_label_recorded_this_event = False
        self._rescan_counter = 0
        self._goal_streak = 0
        self._goal_recorded_this_event = False
        self._pending_goals: list[GoalEvent] = []
        self._vs_streak = 0
        self._vs_recorded_this_match = False
        self._pending_vs_mine_ranks: list[SlotRank] = []
        self._pending_vs_opponent_ranks: list[SlotRank] = []
        self._pending_mine_team_color: Optional[str] = None
        self._pending_opponent_team_color: Optional[str] = None
        # Issue #145: VS画面確定を検知した直後の1フレームだけpop_vs_screen_event()が
        # 返す値。取得されると(popされると)Noneに戻る「取得したら消費される」設計
        self._vs_screen_event: Optional[VsScreenEvent] = None
        # Issue #189: 上記_vs_screen_eventおよび_pending_vs_*系フィールドは
        # バックグラウンドスレッド(_run_vs_ocr)からも書き込まれるため、
        # pop_vs_screen_event()側の読み取り+クリアと衝突しないよう保護する
        self._vs_screen_event_lock = threading.Lock()
        self._vs_ocr_thread: Optional[threading.Thread] = None
        self._match_end_streak = 0
        self._match_end_recorded_this_event = False
        self._match_end_seen = False
        # Issue #190: _match_end_seenはbanner確定時のデバウンス短縮用にすぐ
        # リセットされてしまうため、_finalize()到達時点まで確認結果を持ち越す
        # 別フラグ。OBSシーン切替(in_match)の必須条件にのみ使う
        # (MatchResultの記録自体は従来どおり、このフラグの有無に関わらず行う)
        self._match_end_confirmed_this_match = False
        # Issue #83: OBSシーン切替のトリガー用。VS画面確定でTrue、_finalize()でFalseに戻す
        self._in_match = False
        # Issue #71: セッション内の試合数カウンタ。「試合開始」ログ(VS画面確定時)
        # でのみ増加する。VS画面を見逃した試合では増加しないため、その場合の
        # 「試合終了」「結果」ログは直前に増加させた番号を使い回す(ユーザーと
        # すり合わせ済み。番号がズレるリスクより、見逃しでログ自体が欠落する方を避ける)
        self._session_match_no = 0

    @property
    def current_state(self) -> str:
        """現在の状態("watching" / "tracking_rank" / "cooldown")。テスト等での観測用。"""
        return self._state.label

    @property
    def in_match(self) -> bool:
        """VS画面確定〜試合結果確定(ランク確定含む)までの間True。

        Issue #83: OBSシーン自動切り替えのトリガーに使う(モジュールdocstring参照)。
        """
        return self._in_match

    def pop_vs_screen_event(self) -> Optional[VsScreenEvent]:
        """VS画面を確定した直後の1フレームだけVsScreenEventを返す(Issue #145)。

        process_frame()と同じ「取得したら消費される」設計。呼び出すと内部の
        保持値はNoneに戻るため、main.py側はprocess_frame()を呼ぶたびに毎回
        これも呼び、Noneでなければその場でDBへ即時反映すること。
        """
        with self._vs_screen_event_lock:
            event = self._vs_screen_event
            self._vs_screen_event = None
        return event

    def process_frame(self, frame: np.ndarray) -> Optional[MatchResult]:
        if self._state is _State.WATCHING:
            self._check_for_vs_screen(frame)
            self._check_for_goal(frame)
            self._check_for_match_end(frame)
            return self._watch_for_banner(frame)
        if self._state is _State.TRACKING_RANK:
            return self._track_rank(frame)
        return self._watch_for_banner_absence(frame)

    def _check_for_vs_screen(self, frame: np.ndarray) -> None:
        # デバッグ用: DEBUGレベル時のみVS_ROIの生HSV値を毎フレームログに残す
        # (通常運用のINFOレベルでは出ないため影響なし)。Issue #68で実機の閾値
        # 調整に使用、閾値自体はIssue #116の実測で確定済み(detection.matchmaking参照)
        if logger.isEnabledFor(DEBUG):
            h, s, v = read_vs_roi_hsv(frame)
            top, bottom, middle = read_letterbox_brightness(frame)
            logger.debug(
                "VS_ROI HSV: H=%.2f S=%.2f V=%.2f letterbox(top=%.2f bottom=%.2f middle=%.2f)",
                h,
                s,
                v,
                top,
                bottom,
                middle,
            )
        if not is_vs_screen(frame):
            self._vs_streak = 0
            self._vs_recorded_this_match = False
            return

        self._vs_streak += 1
        if self._vs_streak >= self._vs_screen_confirm_frames and not self._vs_recorded_this_match:
            self._vs_recorded_this_match = True
            self._in_match = True
            self._session_match_no += 1
            logger.info("%d試合目開始", self._session_match_no)
            # Issue #189: read_vs_screen_ranks()は最大16回のPaddleOCR推論を伴い
            # 9〜16秒かかる。process_frame()から同期的に呼ぶとこの間フレーム処理
            # ループ全体がブロックされ、上記のin_match=True(OBSシーン切替の
            # トリガー)がmain.py側に伝わるのもOCR完了まで遅延してしまう
            # (実配信で確認済み、詳細はモジュールdocstring・Issue #189参照)。
            # in_matchの確定は上記で即座に終わらせ、OCR自体は別スレッドに逃がし、
            # 完了後にpending値・VsScreenEventを反映する。この試合が完全に終わる
            # (_finalize())まではvs_recorded_this_matchがTrueのままなので、次の
            # VS画面OCRが重ねて走ることはない(_finalize()側でスレッド完了を待つ)
            match_no = self._session_match_no
            self._vs_ocr_thread = threading.Thread(
                target=self._run_vs_ocr, args=(frame, match_no), daemon=True
            )
            self._vs_ocr_thread.start()

    def _run_vs_ocr(self, frame: np.ndarray, match_no: int) -> None:
        mine_ranks, opponent_ranks = read_vs_screen_ranks(frame)
        mine_team_color, opponent_team_color = read_team_colors(frame)
        self._pending_vs_mine_ranks = mine_ranks
        self._pending_vs_opponent_ranks = opponent_ranks
        self._pending_mine_team_color = mine_team_color
        self._pending_opponent_team_color = opponent_team_color
        # Issue #145: 試合結果確定(MatchResult)を待たず、main.py側が
        # 次にprocess_frame()を呼んだタイミングですぐDBへ反映できるようにする
        # (pop_vs_screen_event参照)
        event = VsScreenEvent(
            mine_ranks=mine_ranks,
            opponent_ranks=opponent_ranks,
            mine_team_color=mine_team_color,
            opponent_team_color=opponent_team_color,
        )
        with self._vs_screen_event_lock:
            self._vs_screen_event = event
        # Issue #121: ゴール検知(_check_for_goal)と同じく、DBへの記録タイミング
        # (main.py側のsave_vs_slot_ranks時)を待たず、OCRが完了した時点で
        # 読み取ったランクをそのまま報告する
        logger.info(
            "%d試合目 VS画面ランク: mine=%s opponent=%s",
            match_no,
            mine_ranks,
            opponent_ranks,
        )
        logger.info(
            "%d試合目 チームカラー: mine=%s opponent=%s",
            match_no,
            mine_team_color,
            opponent_team_color,
        )

    def _check_for_match_end(self, frame: np.ndarray) -> None:
        if not is_match_end_screen(frame):
            self._match_end_streak = 0
            self._match_end_recorded_this_event = False
            return

        self._match_end_streak += 1
        if self._match_end_streak >= self._match_end_confirm_frames and not self._match_end_recorded_this_event:
            self._match_end_recorded_this_event = True
            # is_match_end_screenは色ベースの候補判定のため、「延長戦」「キックオフ」等の
            # 誤検知をここでOCRにより除外する(detection.match_end参照)
            if confirm_match_end_text(frame):
                self._match_end_seen = True
                self._match_end_confirmed_this_match = True
                logger.info("%d試合目 試合終了", self._session_match_no)

    def _check_for_goal(self, frame: np.ndarray) -> None:
        if not is_goal_event(frame):
            self._goal_streak = 0
            self._goal_recorded_this_event = False
            return

        self._goal_streak += 1
        if self._goal_streak >= self._goal_confirm_frames and not self._goal_recorded_this_event:
            self._goal_recorded_this_event = True
            # is_goal_eventは色ベースの候補判定のため、青空・スタジアム天蓋の映り込み
            # (Issue #186)等の誤検知をここでOCRにより除外する(detection.goal参照)
            if not confirm_goal_text(frame):
                logger.info(
                    "ゴール候補を検知しましたが、得点者名パネルのラベルを確認できなかったため誤検知として無視します"
                )
                return

            scorer = read_scorer_name(frame)
            assist = read_assist_name(frame)
            # Issue #71: OCRの誤読診断のため、信頼度スコア込みの実名をDEBUGレベルに
            # 限り出す(CLAUDE.md「ログ方針」の例外)
            logger.debug("ゴール検知: scorer=%s assist=%s", scorer, assist)

            scorer_name = scorer[0] if scorer is not None else None
            assist_name = assist[0] if assist is not None else None
            # Issue #217: オウンゴールは得点者名パネル自体が表示されないため
            # scorer_name/assist_nameは常にNoneのまま(detection.goalのdocstring参照)。
            # 判定結果は見えたものをそのまま報告するのみで、GOAL_RECORD_MODEに応じた
            # 記録可否の判断は永続化層(database.db.save_goal)の責務のまま変更しない
            is_own_goal = is_own_goal_event(frame)
            # Issue #86: 検知した瞬間に得点者・アシスト名を許可リストの判定結果に
            # よらずINFOレベルで表示する(2026-07決め事、CLAUDE.md「ログ方針」参照。
            # 個人のローカル環境のみでの運用のため、許可リスト外の実名がログに
            # 残ること自体は許容する)。ここでの判定はログ表示用の見込みに過ぎず、
            # 実際にDBへ記録する/しないの判定は引き続き永続化層(database.db.
            # save_goal)の責務のまま変更しない。Issue #88でGOAL_RECORD_MODEの
            # 3モード(all/allowlist/allowlist_redact)に合わせて3値表示にした
            mode = get_goal_record_mode()
            scorer_allowed = scorer_name is not None and is_allowed_player(scorer_name)
            assist_allowed = assist_name is not None and is_allowed_player(assist_name)
            if is_own_goal:
                status = "記録対象(オウンゴール)" if mode == "all" else "オウンゴールのため記録対象外"
            elif mode == "all":
                status = "記録対象"
            elif not scorer_allowed and not assist_allowed:
                status = "許可リスト外のため記録対象外"
            elif mode == "allowlist_redact" and (
                not scorer_allowed or (assist_name is not None and not assist_allowed)
            ):
                status = "一部redactして記録対象"
            else:
                status = "記録対象"
            logger.info(
                "ゴール検知: scorer=%s assist=%s (%s)",
                scorer_name,
                assist_name,
                status,
            )

            self._pending_goals.append(
                GoalEvent(
                    scorer_name=scorer_name,
                    assist_name=assist_name,
                    detected_at=now_jst(),
                    is_own_goal=is_own_goal,
                )
            )

    def _watch_for_banner(self, frame: np.ndarray) -> Optional[MatchResult]:
        result = classify_banner(frame)
        if result is not None and result == self._banner_candidate:
            self._banner_streak += 1
        elif result is not None:
            self._banner_candidate = result
            self._banner_streak = 1
        else:
            self._banner_candidate = None
            self._banner_streak = 0

        # Issue #76: 「試合終了」を確認できていれば短いデバウンス、できていなければ
        # 従来どおりの安全側の長いデバウンスを使う(モジュールdocstring参照)
        required_streak = (
            self._banner_confirm_frames_after_match_end if self._match_end_seen else self._banner_confirm_frames
        )
        if self._banner_streak >= required_streak:
            self._pending_result = self._banner_candidate
            # バナー確定直後 = ランク変動アニメーションが始まる前 = 常にコンパクト表示
            precise_result = read_precise_rank(frame, GAUGE_ROI_COMPACT, RANK_NUMBER_ROI_COMPACT)
            if precise_result is not None:
                self._pending_rank_before_tier, self._pending_rank_before = precise_result
            else:
                self._pending_rank_before_tier = None
                self._pending_rank_before = None
                logger.info(
                    "結果バナー確定時点でランクバッジを読み取れませんでした"
                    "(バッジ非表示、または読み取り失敗の可能性)"
                )
            logger.info(
                "%d試合目の結果: %s (ランク: %s)",
                self._session_match_no,
                _BANNER_RESULT_LABELS[self._pending_result],
                self._pending_rank_before if self._pending_rank_before is not None else "なし",
            )
            self._banner_candidate = None
            self._banner_streak = 0
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            self._grace_candidate_rank_tier = None
            self._latest_gauge_fill = None
            self._promotion_confirmed_this_match = False
            self._demotion_confirmed_this_match = False
            self._demotion_label_streak = 0
            self._demotion_label_recorded_this_event = False
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._state = _State.TRACKING_RANK
            # 「試合終了」の確認は今回の結果バナー確定にのみ使うため、ここでリセットする
            self._match_end_seen = False
        return None

    def _track_rank(self, frame: np.ndarray) -> Optional[MatchResult]:
        self._check_for_demotion_label(frame)

        if is_league_change_screen(frame):
            self._rank_phase = _RankPhase.IN_LEAGUE_CHANGE
            self._grace_counter = 0
            self._promotion_confirmed_this_match = True
            return None

        if self._rank_phase is _RankPhase.IN_LEAGUE_CHANGE:
            # 演出が終わった直後。新しいランク値が安定するのを最初から待ち直す
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._rank_phase = _RankPhase.WAITING_STABLE
            return None

        # Issue #209: 暗転(画面全体が真っ黒)を検知したら、grace_counter・
        # near_tier_cap・バナー消灯確認等の状態に関わらず直ちに確定する。
        # この暗転は試合結果〜ランク確定演出(昇格演出を含む)が完全に終わった
        # 直後にのみ現れるため、候補値を一度でも読み取れていればそれを採用して
        # よい(モジュールdocstring参照)。is_stable系のロジックより前で
        # チェックする必要がある: 暗転自体が直前フレームとの急激な変化になり
        # StabilityMonitorを不安定化させてしまい、素通りするとWAITING_STABLEへ
        # 戻ってこの確定に到達できなくなるため
        if self._grace_candidate_rank_tier is not None and is_full_blackout(frame):
            return self._begin_finalize(self._grace_candidate_rank_tier, self._current_grace_rank())

        if self._rank_phase is _RankPhase.RESCAN_WAIT:
            return self._continue_rescan_wait(frame)

        was_stable = self._rank_monitor.is_stable
        is_stable = self._rank_monitor.update(frame)

        if self._rank_phase is _RankPhase.WAITING_STABLE:
            if is_stable and not was_stable:
                self._rank_phase = _RankPhase.GRACE
                self._grace_counter = 0
                # 安定した瞬間(まだ画面が遷移し始めていない良いフレーム)でOCRしておく。
                # 猶予期間の最後まで待つとバナー自体が消えかけの不安定なフレームに
                # なりOCRが失敗しうるため、帯番号はここで確定させて使い回す。
                # 微小なノイズで安定が何度か途切れて再試行することがあるが、
                # 直近の試行がたまたま失敗しても直前までの正常な読み取り結果を
                # 上書きしないよう、Noneの場合は前回値を保持する。
                # TRACKING_RANK中(アニメーション開始後)は常に拡大表示
                precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED, RANK_NUMBER_ROI_ENLARGED)
                if precise_result is not None:
                    self._grace_candidate_rank_tier, precise = precise_result
                    self._latest_gauge_fill = precise - self._grace_candidate_rank_tier
            return None

        # _RankPhase.GRACE: 安定はしたが、直後に昇格/降格演出が始まらないか
        # league_change_grace_frames分だけ様子を見る。バナー自体が消えたら
        # 演出は来ないと判断し、猶予期間を待たずに確定してよい
        if not is_stable:
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            return None

        # Issue #178: ゲージの塗りつぶし(HSVベースの軽量な色判定)は毎フレーム
        # 読み取り、常に最新値で上書きし続ける。安定判定(StabilityMonitor)の
        # タイミングは、--video実行時の実時間再生+FfmpegFrameReaderのフレーム
        # 間引きの影響でずれることがあり、確定した瞬間のスナップショットを
        # 1回だけ使う方式だと、実際にはまだ動いている途中の値を掴んでしまう
        # ことが実データ(本番DBで誤検知が見つかったmatches.id=19/20の元動画)で
        # 確認された。帯番号は数値OCR(重い処理)のため頻度は変えない。
        # _grace_counterは「最後にゲージ変化を検知してから何フレーム経ったか」も
        # 兼ねる(変化を検知した瞬間に0へリセットする)ため、下記の「バナー消灯
        # 即確定」判定にもそのまま使う
        fill = read_rank_gauge_fill(frame, GAUGE_ROI_ENLARGED)
        if fill is not None:
            if self._latest_gauge_fill is not None and abs(fill - self._latest_gauge_fill) > RANK_RECHECK_CHANGE_TOLERANCE:
                # まだゲージが動いている途中とみなし、猶予期間をやり直す
                self._grace_counter = 0
            self._latest_gauge_fill = fill

        # Issue #178: 結果バナー(勝敗テキスト)はランクゲージのアニメーションより
        # 先に消えることがあるため、バナーが消えた瞬間に無条件で確定するのではなく、
        # 直近rank_recheck_interval_frames分はゲージの変化が無かったことを確認して
        # から確定する(まだ変化が続いている間はこの分岐を素通りしてgrace_counterの
        # 通常のタイムアウト待ちに合流する)。
        #
        # Issue #209: ただしゲージが帯の上限付近(LEAGUE_CHANGE_IMMINENT_FILL_THRESHOLD
        # 以上)のときはこの早期確定を使わない。昇格演出が始まる直前は、ゲージが
        # 上限に到達して一時的に本当に動かなくなる「踊り場」ができ、この間に
        # バナーのテキストがたまたま一瞬消えるとこの条件を満たしてしまい、
        # 昇格演出(is_league_change_screen)が始まる前に確定してしまうことが
        # 実データ(fixtures/videos/30・42番)で判明した。この場合は早期確定を
        # 見送り、通常どおりleague_change_grace_frames(既定5秒相当)満了まで待つ
        # ことで、昇格演出が始まればis_league_change_screen()の分岐(このメソッド
        # 冒頭)が先に捕捉できるようにする
        near_tier_cap = (
            self._latest_gauge_fill is not None
            and self._latest_gauge_fill >= LEAGUE_CHANGE_IMMINENT_FILL_THRESHOLD
        )
        if (
            classify_banner(frame) is None
            and self._grace_counter >= self._rank_recheck_interval_frames
            and not near_tier_cap
        ):
            self._fill_grace_candidate_if_missing(frame)
            return self._begin_finalize(self._grace_candidate_rank_tier, self._current_grace_rank())

        self._grace_counter += 1

        # ピクセル差分では検知できない緩やかな帯番号の変化を見逃さないよう、
        # 一定間隔で読み直して候補の帯番号が古くなっていないか確認する
        # (数値OCRは重いためここだけ間引く。ゲージ小数部は上記で毎フレーム追跡済み)
        if self._grace_counter % self._rank_recheck_interval_frames == 0:
            tier = read_rank(frame, RANK_NUMBER_ROI_ENLARGED)
            if tier is not None and tier != self._grace_candidate_rank_tier:
                self._grace_candidate_rank_tier = tier
                self._grace_counter = 0
                return None

        if self._grace_counter < self._league_change_grace_frames:
            return None
        self._fill_grace_candidate_if_missing(frame)
        return self._begin_finalize(self._grace_candidate_rank_tier, self._current_grace_rank())

    def _check_for_demotion_label(self, frame: np.ndarray) -> None:
        """降格ラベル(「降格」の吹き出し)を検知する(Issue #176)。

        is_demotion_label_candidate()(軽量な輝度判定)がdemotion_label_confirm_frames回
        連続したタイミングで1回だけconfirm_demotion_label_text()を呼んでOCRで
        確認する(is_goal_event/confirm_goal_textと同じ2段構成、モジュールdocstring
        参照)。確認できれば_demotion_confirmed_this_matchに保持し、_finalize()まで
        持ち越す。
        """
        if not is_demotion_label_candidate(frame):
            self._demotion_label_streak = 0
            self._demotion_label_recorded_this_event = False
            return

        self._demotion_label_streak += 1
        if (
            self._demotion_label_streak >= self._demotion_label_confirm_frames
            and not self._demotion_label_recorded_this_event
        ):
            self._demotion_label_recorded_this_event = True
            if confirm_demotion_label_text(frame):
                self._demotion_confirmed_this_match = True
                logger.info("%d試合目 降格ラベルを検知しました", self._session_match_no)

    def _current_grace_rank(self) -> Optional[float]:
        """帯番号(OCR)+ゲージ小数部(継続追跡している最新値)を組み合わせた現在値。

        小数部が一度も読めていない場合のみ0.0扱いにする(read_precise_rankの
        フォールバックと同じ考え方)。
        """
        if self._grace_candidate_rank_tier is None:
            return None
        fill = self._latest_gauge_fill if self._latest_gauge_fill is not None else 0.0
        return self._grace_candidate_rank_tier + fill

    def _fill_grace_candidate_if_missing(self, frame: np.ndarray) -> None:
        """確定直前の時点で候補値が一度も読めていない場合のみ、最後にもう一度読み取りを試みる。

        実キャプチャ(FfmpegFrameReader)は処理が追いつかない間のフレームを間引くため、
        GRACE突入直後にたまたまサンプリングしたフレームがバッジの遷移中で
        読み取れず、そのまま候補が更新されないままバナーが消える(または
        猶予期間が満了する)ことがありうる。既に有効な候補があればここでは
        何もしない(古い正常値を上書きしない)。

        呼び出し元はいずれも_RankPhase.GRACE中(ランク変動アニメーション開始後)
        のため、常に拡大表示のROIを使う。
        """
        if self._grace_candidate_rank_tier is not None:
            return
        precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED, RANK_NUMBER_ROI_ENLARGED)
        if precise_result is not None:
            self._grace_candidate_rank_tier, precise = precise_result
            self._latest_gauge_fill = precise - self._grace_candidate_rank_tier

    def _begin_finalize(self, tier: Optional[int], rank: Optional[float]) -> Optional[MatchResult]:
        """帯番号確定前の最終チェック(Issue #136)。不自然な急変ならすぐには確定せず、
        数フレーム後に再スキャンする。
        """
        if self._is_tier_change_plausible(tier):
            return self._finalize(tier, rank)
        logger.warning(
            "%d試合目: 帯番号が不自然に変化しています(before=%s after=%s)。"
            "%dフレーム後に再スキャンします",
            self._session_match_no,
            self._pending_rank_before_tier,
            tier,
            self._rank_tier_rescan_wait_frames,
        )
        self._rescan_counter = 0
        self._rank_phase = _RankPhase.RESCAN_WAIT
        return None

    def _continue_rescan_wait(self, frame: np.ndarray) -> Optional[MatchResult]:
        self._rescan_counter += 1
        if self._rescan_counter < self._rank_tier_rescan_wait_frames:
            return None
        precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED, RANK_NUMBER_ROI_ENLARGED)
        if precise_result is not None:
            tier, rank = precise_result
        else:
            tier, rank = self._grace_candidate_rank_tier, self._current_grace_rank()
        if self._is_tier_change_plausible(tier):
            return self._finalize(tier, rank)
        logger.warning(
            "%d試合目: 再スキャンでも帯番号が不自然なままのため(after=%s)、"
            "ゲージ小数部の連続性から補正します",
            self._session_match_no,
            tier,
        )
        corrected_tier, corrected_rank = self._infer_tier_from_gauge_continuity(tier, rank)
        return self._finalize(corrected_tier, corrected_rank)

    def _is_tier_change_plausible(self, tier_after: Optional[int]) -> bool:
        """試合前後の帯番号の変化が、ゲームの仕様上ありうるものか検証する(Issue #136)。

        1試合での帯変化は昇格/降格いずれも1帯までしか起こらない。さらに
        「勝ったら降格しない/負けたら昇格しない」というゲーム仕様(ユーザー確認済み)
        より、昇格(+1)は勝ちかつ昇格演出(is_league_change_screen)を確認できて
        いる場合のみ、降格(-1)は負けの場合のみ許容する。引き分けはゲージ自体が
        全く動かない仕様のため、変化無し(0)以外は常に不自然とみなす。

        Issue #202: 変化無し(0)は上記に加えて、降格ラベル(is_demotion_label_candidate/
        confirm_demotion_label_text、Issue #176)を確認できている負け試合では不自然と
        みなす。降格ラベルという独立信号で降格の発生自体は確認できているにも
        関わらず帯番号OCRが「変化なし」に化けてしまったケースを、他の帯番号急変
        ケースと同じ再スキャン経路(_begin_finalize→_continue_rescan_wait→
        _infer_tier_from_gauge_continuity)に合流させ、最終的にtier_before-1として
        記録できるようにするため。
        """
        tier_before = self._pending_rank_before_tier
        if tier_before is None or tier_after is None:
            return True
        delta = tier_after - tier_before
        if delta == 0:
            return not (self._pending_result == "lose" and self._demotion_confirmed_this_match)
        if delta == 1:
            return self._pending_result == "win" and self._promotion_confirmed_this_match
        if delta == -1:
            return self._pending_result == "lose"
        return False

    def _infer_tier_from_gauge_continuity(
        self, tier_ocr: Optional[int], rank_value: Optional[float]
    ) -> tuple[Optional[int], Optional[float]]:
        """再スキャンしても帯番号が不自然なままの場合の最終フォールバック(Issue #136)。

        数値OCR(帯番号)ではなく、ゲージの溜まり具合(HSVベースの独立信号、
        read_precise_rankの戻り値からtier_ocrを差し引いて復元する)の連続性と
        勝敗結果を使って帯番号を推測し直す。

        昇格はis_league_change_screen()で独立確認済みの場合のみそれを正として
        採用する。降格はIssue #176でis_demotion_label_candidate()/
        confirm_demotion_label_text()による独立確認信号を追加したため、
        この試合中に確認できていればそれを優先して1帯下げる。確認できて
        いない場合は従来どおり「負けているのにゲージ小数部が
        RANK_TIER_WRAP_MIN_MAGNITUDEを超えて増えて見える(0を割り込んで前の帯に
        巻き戻ったように見える)」という間接的な判定にフォールバックする
        (見逃しても既存の正しさは損なわれない設計、モジュールdocstring参照)。
        それ以外(勝ち・引き分け、または負けでも矛盾がしきい値未満・降格ラベルも
        未確認)は帯番号を変えず、小数部だけをそのまま採用する(ゲージが全く
        動かない引き分けも含め、変な値に書き換えないという方針)。
        """
        tier_before = self._pending_rank_before_tier
        rank_before = self._pending_rank_before
        if tier_before is None or rank_before is None or rank_value is None:
            return tier_ocr, rank_value

        frac_before = rank_before - tier_before
        frac_after = rank_value - tier_ocr if tier_ocr is not None else rank_value - tier_before

        if self._promotion_confirmed_this_match:
            return tier_before + 1, tier_before + 1 + frac_after

        if self._pending_result == "lose" and (
            self._demotion_confirmed_this_match or frac_after - frac_before > RANK_TIER_WRAP_MIN_MAGNITUDE
        ):
            return tier_before - 1, tier_before - 1 + frac_after

        return tier_before, tier_before + frac_after

    def _finalize(self, rank_after_tier: Optional[int], rank_after: Optional[float]) -> MatchResult:
        # Issue #189: VS画面OCR(_run_vs_ocr)はバックグラウンドスレッドで実行される。
        # 通常は試合が終わる頃には完了しているはずだが(OCR自体は最大16秒、試合は
        # 数分続く)、念のためここで完了を待ってから_pending_vs_*系フィールドを
        # 読み取る(未完了のままMatchResultを組むと空リストのまま記録されてしまう)
        if self._vs_ocr_thread is not None:
            self._vs_ocr_thread.join()
            self._vs_ocr_thread = None
        if rank_after is None:
            logger.info(
                "試合終了時点でもランクバッジを読み取れませんでした"
                "(バッジ非表示、または読み取り失敗の可能性)"
            )
        # league_changedはゲージの溜まり具合を含まない帯番号(整数)同士で判定する。
        # 小数のランク値同士で比較すると、帯は変わっていないのにゲージが
        # 僅かに増減しただけで昇格/降格と誤判定してしまうため
        league_changed = None
        if self._pending_rank_before_tier is not None and rank_after_tier is not None:
            if rank_after_tier > self._pending_rank_before_tier:
                league_changed = "up"
            elif rank_after_tier < self._pending_rank_before_tier:
                league_changed = "down"

        match_result = MatchResult(
            result=self._pending_result,
            rank_before=self._pending_rank_before,
            rank_after=rank_after,
            league_changed=league_changed,
            detected_at=now_jst(),
            goals=self._pending_goals,
            vs_mine_ranks=self._pending_vs_mine_ranks,
            vs_opponent_ranks=self._pending_vs_opponent_ranks,
            mine_team_color=self._pending_mine_team_color,
            opponent_team_color=self._pending_opponent_team_color,
        )
        self._pending_result = None
        self._pending_rank_before = None
        self._pending_rank_before_tier = None
        self._promotion_confirmed_this_match = False
        self._demotion_confirmed_this_match = False
        self._demotion_label_streak = 0
        self._demotion_label_recorded_this_event = False
        self._pending_goals = []
        self._goal_streak = 0
        self._goal_recorded_this_event = False
        self._pending_vs_mine_ranks = []
        self._pending_vs_opponent_ranks = []
        self._pending_mine_team_color = None
        self._pending_opponent_team_color = None
        self._vs_streak = 0
        self._vs_recorded_this_match = False
        # Issue #190: 「試合終了」バナーをOCRで確認できた試合に限りOBSシーン切替
        # (in_match=False)を行う。確認できなかった試合(実プレイ中の背景誤検知が
        # banner_confirm_framesを突破した可能性を否定できない)はin_matchをTrueの
        # ままにし、試合中シーンに留める。MatchResultの記録自体はこの確認結果に
        # 関わらず常に行う(モジュールdocstring参照)
        if self._match_end_confirmed_this_match:
            self._in_match = False
        else:
            logger.info(
                "%d試合目: 「試合終了」バナーを確認できなかったためOBSシーン切替を見送ります"
                "(試合中シーンのまま維持、次に確認できた試合まで持ち越します)",
                self._session_match_no,
            )
        self._match_end_confirmed_this_match = False
        self._absence_streak = 0
        self._state = _State.COOLDOWN
        return match_result

    def _watch_for_banner_absence(self, frame: np.ndarray) -> Optional[MatchResult]:
        if classify_banner(frame) is None:
            self._absence_streak += 1
        else:
            self._absence_streak = 0

        if self._absence_streak >= self._banner_absence_confirm_frames:
            self._absence_streak = 0
            self._state = _State.WATCHING
        return None
