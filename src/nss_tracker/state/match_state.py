"""試合の状態遷移を管理する状態機械。

CLAUDE.md記載の「試合後の状態遷移」(結果バナー表示→ランク変動アニメーション→
ランク確定→暗転→マッチング画面)を、banner/rank_ocr/motion/league_changeの
各検知結果をつないで管理する。フレームを1枚ずつ process_frame() に渡すと、
試合の記録が完了した瞬間だけ MatchResult を返す。

「暗転」を明示的な輝度閾値で検知するのではなく、結果バナーが一定時間
確実に消えたこと(banner=Noneがbanner_absence_confirm_seconds回連続)を
もって次の試合への再武装(WATCHING状態への復帰)とみなす。暗転〜マッチング
画面のどこかで必ずバナーが消えるため、この方が輝度閾値を新たに調整するより
頑健(検証済みの banner判定・デバウンスの仕組みをそのまま再利用できる)。

banner判定は単体だと一瞬誤検知しうるため(detection.banner参照)、ここでも
banner_confirm_seconds 秒以上連続した判定のみを採用する(デフォルト2秒、
Issue #67対応、後述)。

Issue #388: このクラスのデバウンス閾値(banner_confirm_seconds等、末尾が
_secondsのコンストラクタ引数)はすべて実時間(秒)で持つ。以前はフレーム数で
持ち、呼び出し側(main.py)が起動時のfpsから`round(fps * 秒数)`へ換算していたが、
検知ループの実効fpsは処理内容(OCR負荷・OBS Virtual Camera側の詰まり等)次第で
大きく変動することが実配信で判明した(Issue #383/#387)。実効fpsが想定より
落ちると、同じフレーム数を稼ぐのに想定より長い実時間がかかり、「1秒デバウンス」
のつもりの閾値が実際には数倍の実時間デバウンスとして働いてしまい、表示時間の
短い結果(「勝ち」バナー等)の確定を取りこぼす実害が出た(#387で解析、詳細は
#388参照)。`now_fn`(既定`time.monotonic`、テストでは差し替え可能)で取得した
実時刻を基準に「その状態が実際に何秒持続したか」で判定することで、実効fpsが
変動してもデバウンスの意味(秒数)が変わらないようにした。process_frame()の
呼び出し自体が実フレーム到着時にしかトリガーされないため、フレームが1枚も
来ないまま経過秒数だけが人工的に進むことは構造的に起きない(フレーム数の
下限を別途設けなくても安全、ユーザーとの相談で確認済み)。

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
根本的な検知改善は今後の課題とし、今回は対症療法としてbanner_confirm_secondsを
1秒から2秒に延長した(誤検知は1.3秒程度しか持続せず、本物のバナーは数秒以上
表示され続けるため、2秒あれば今回のサンプルは確実に防げる。検知遅延が数秒増える
が、バックグラウンドでの記録用途のため実用上の影響はない)。回帰テストは
fixtures/videos/21_goal_event_false_positive_win_blue_4-3.mp4
(tests/test_match_state.pyのtest_match_state_machine_matches_expected_metadata、
fixtures/videos/metadata.json参照)。

Issue #76: Issue #67の2秒デバウンスは対症療法であり検知が遅くなる副作用がある。
これを改善するため、試合の本当の終了時点にのみ表示される「試合終了」バナー
(detection.match_end参照)を補助信号として使う。WATCHING中に
is_match_end_screen()を毎フレーム軽量にチェックし、match_end_confirm_seconds回
連続したタイミングで1回だけconfirm_match_end_text()を呼んでOCRで文字を確認する
(「試合終了」と色味が酷似する「延長戦」「キックオフ」バナーとの誤認識を防ぐため。
detection.match_end参照)。「試合終了」を確認できていれば、_watch_for_banner()の
確定に必要なbanner_streakの閾値をbanner_confirm_seconds_after_match_end
(短い、デフォルトはIssue #67修正前と同じ1秒相当)に切り替える。確認できていない
場合は通常どおりbanner_confirm_seconds(長い、2秒相当)のままとする。

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
goal_confirm_seconds回連続したタイミングで1回だけconfirm_goal_text()を呼び、
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
確定させず、league_change_grace_seconds分だけ様子を見て、その間に演出が現れたら
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
処理)はrank_recheck_interval_secondsおきに読み直して古くなっていないか確認する。

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

Issue #178で追加した、GRACE中に「バナー消灯+直近rank_recheck_interval_seconds分
ゲージが変化していない」ことを合図に早期確定するパスは、Issue #209の対策
(_latest_gauge_fillが帯上限付近のときはこの早期確定を使わない、という
near_tier_capガード)を挟んでもなお、Issue #235で別系統の不具合を引き起こして
いたことが実データ(2026-08-05の実機テストセッション)で判明した。結果バナー
(勝敗テキスト)が消えて次の画面(試合間の豆知識画面等)へ切り替わる遷移演出
(ワイプ/フラッシュ)の最中、ゲージ読み取りROI(GAUGE_ROI_ENLARGED)の明度が
一瞬跳ね上がることが実測で分かった(fill値が0.097→0.862→0.000と、0.6秒程度で
急騰・急落する)。この遷移演出は昇格演出の有無に関わらずどの試合でも起こり
うるため、near_tier_capガードでは防げない。バナー消灯直後の数フレームで
この遷移演出のノイズを「安定したゲージ値」と誤認し早期確定してしまうケースが
実測で複数見つかった(負けているのにrank_afterが上昇して記録される、
リーグ昇格演出が始まる前に確定してしまい昇格そのものを見逃す、等)。

Issue #235でこの早期確定パス自体を廃止した。GRACE中の確定手段は「本当の
暗転(is_full_blackout)を検知した瞬間の即時確定」と「league_change_grace_seconds
(既定5秒)満了による確定」の2つのみになった。バナー消灯後すぐには確定せず
常に暗転または猶予満了まで待つ形になるため、昇格演出が無い普通の試合でも
確定までの待ち時間が数秒(最大5秒)伸びるが、バックグラウンドで動くロガー
でありこの程度の遅延の実害は薄いと判断した(ユーザー確認済み)。

ただし「暗転まで待てば必ず正しい値が掴める」わけではないことも実データで
判明した。上記のワイプ演出による急騰ノイズが収まった後、ランクバッジ自体が
画面外へ消え、シーン全体が暗転へ向けて徐々にフェードしていく区間が続くことが
分かった(2026-08-05実機テストセッション・3試合目の実測: 急騰が収まった
0.65秒後には、バッジが写っていない芝生だけの画面が徐々に暗くなっていく
様子を確認)。この間もread_rank_gauge_fillは毎フレーム呼ばれ続けるため、
「バッジが無い(≒塗りつぶし0%に見える)」フレームを暗転検知の直前まで
_latest_gauge_fillへ反映してしまい、本当の最終値(実測0.10前後、ワイプ演出が
始まる前の0.55秒間は完全に一定だった)ではなく0.0に近い値で確定してしまう
別の不具合があった。

Issue #235(追加対応)で、_latest_gauge_fillの更新自体にもデバウンスを
導入した。生値を直接反映するのではなく、_pending_gauge_fill/
_pending_gauge_debounceで直近rank_recheck_interval_seconds秒連続して同じ値
(RANK_RECHECK_CHANGE_TOLERANCE許容)が続いて初めて_latest_gauge_fillを
更新する(banner_confirm_seconds等、他の検知と同じデバウンスの考え方)。
ワイプ演出の急騰・フェードによる急落とも、値が一方向に動き続けるため
連続一致の条件を満たさず、直前の確定値(上記の例では0.10)が保持され
続けたまま暗転を迎える。本当にゲージが緩やかに動き続けているケース
(Issue #178)は、動きが止まって安定すればそのぶん遅れて正しく確定値に
反映されるため、既存の挙動を壊さない。

ただし上記のleague_change_grace_seconds満了待ちには依然として上限時間が
あるため、理論上はそれより長く昇格演出の開始が遅れた場合(例: 何らかの理由で
結果画面のまま長時間状態が変化しない)、同じ形の見逃しが再現しうる。この
残存リスクに対する最終的な安全装置として、CLAUDE.md記載の「4. 暗転」
(ランク確定〜昇格演出を含む一連の演出が完全に終わった直後、マッチング画面に
戻る前に必ず一度全画面が真っ黒になる区間、detection.motion.is_full_blackout
参照)を検知したら、GRACE期間の経過状況に一切関わらず直ちに確定するように
した(Issue #209、Issue #235で早期確定パス自体を廃止した後もこの安全装置は
そのまま残している)。暗転はランク確定と
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
並行してチェックする。banner判定と同じデバウンス(vs_screen_confirm_seconds回
連続)で確定させ、確定した瞬間に1回だけdetection.vs_rank.read_vs_screen_ranks()
を呼び出してMatchResult.vs_mine_ranks/vs_opponent_ranksとして払い出す
(detection.vs_rank側のOCRは重い処理のため、CLAUDE.mdのサンプリング戦略どおり
毎フレームは呼ばない)。当初Issue #39では「VS画面検知は任意のエンリッチとし、
見逃しても既存の結果バナー起点フローは従来通り動作させる」と定めていたが、
Issue #229でこの方針を転換した(下記Issue #229参照)。現在はVS画面を確認
できない試合は結果バナー自体を誤検知とみなして記録しないため、
vs_mine_ranks/vs_opponent_ranksが空になるケースは基本的に発生しない。

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
唯一の切り替えタイミングとした。Issue #229でVS画面確定を試合の区切りの必須条件に
したため、VS画面を見逃した場合はそもそも結果バナー自体が記録されず_finalize()にも
到達しないので、in_matchはTrueにならず試合中シーンへ切り替わらない(この点は
Issue #39時代からの帰結としては変わらないが、理由が「見逃しても許容する」から
「記録自体が起きない」に変わった)。

Issue #145: 対戦相手ランク比較ウィジェット(web/server.py)は、試合結果確定
(MatchResult、試合終了後にまとめて払い出される)を待たず、VS画面を確定した
瞬間にDBへ反映して即座に表示を更新したい。process_frame()の戻り値
(MatchResultは試合終了時の1回だけ)とは別に、`pop_vs_screen_event()`で
「VS画面を確定した直後の1フレームだけ」読み取り結果を取得できるようにする
(in_matchのような常時参照可能なプロパティではなく、process_frame()と同じ
「取得したら消費される」設計。main.py側がprocess_frame()呼び出しのたびに
ポーリングし、Noneでなければその場でDBへ即時反映する)。

Issue #190: 実プレイ中(ゴール演出とは無関係な通常プレイ中)の背景誤検知が
banner_confirm_seconds(2秒デバウンス)を突破し、OBSシーンが誤って試合中→
試合間(ワイプ)へ切り替わってしまう事象が実配信で確認された。特にランクを
賭けない試合(rank_before=Noneのため帯番号ベースの安全装置が一切効かない)は、StabilityMonitorの「安定」判定さえ誤検知フレームで
たまたま満たされれば、あとはbanner_confirm_secondsの2秒デバウンスだけが最後の
砦になる。配信者体験として「マッチング待機中に誤って試合中シーンのままになる」
より「実プレイ中に誤ってワイプへ切り替わる」方がはるかに困るという優先順位が
示されたため、_check_for_match_end()で「試合終了」バナーのOCR確認
(confirm_match_end_text)ができた試合に限り、_finalize()でin_matchをFalseに
戻す(OBSシーン切替を実行する)ことにした。確認できなかった試合は、
MatchResultの記録自体(勝敗・ランク)は従来どおり行うが、in_matchはTrueの
ままにする(OBSシーン切替は見送り、試合中シーンに留まる)。既存の
banner_confirm_seconds_after_match_end(デバウンス短縮)用途とは別に
_match_end_confirmed_this_matchで確認結果を_finalize()まで持ち越す
(_match_end_seenは短縮用のフラグのままbanner確定時にリセットされるため、
そのままでは_finalize()到達時点で常にFalseになってしまう)。

見逃した場合、in_matchはその試合の終了時点ではFalseに戻らず、次の試合の
VS画面確定(既にTrueなので実質no-op)を経て、次にmatch_endを確認できた
試合の_finalize()で初めてFalseに戻る。「見逃した試合の間は試合中シーンに
居座り続ける」形になるが、これはユーザーが許容すると明言した失敗方向であり、
DB記録自体は毎試合従来どおり行われるため実害は無い(モジュールトップの
Issue #76と同じ「見逃しても既存フローの正しさは損なわれない」設計)。

あわせて、match_end_confirm_seconds(色候補判定→OCR確認までのデバウンス)を
1フレームに短縮した。このデバウンスは実質「誤検知を防ぐ安全マージン」としては
機能しておらず、実際に真偽を決めているのはOCRの文字一致(confirm_match_end_text)
そのものである(色条件を満たした最初のフレームで即OCRを呼んでも、「延長戦」
「キックオフ」等の誤った文字列であればOCR側で弾かれるため誤検知には
つながらない)。むしろ唯一実測されている不具合(29_lose_blue_hdr_off.mp4の
frame 958、表示が消える直前の縮小アニメーションでOCRが失敗したケース)は
「確認が遅すぎて表示終了直前の不安定なフレームに当たった」方向のリスクのため、
デバウンスを縮めて可能な限り早いフレームで1回きりのOCRを実行する方が安全。

Issue #423: 「試合終了」をOCR確認できた区間(_match_end_seenがTrueの間)は、
BANNER_ROISの実測値(H/S/V/hue_std)をDEBUGログに残す(_log_banner_stats参照)。
2026-09-08の専用部屋配信で負けバナーが1度も検知されず8試合が丸ごと消えた際、
「classify_banner()が何を見てNoneを返したのか」を示すデータがどこにも無く原因を
特定できなかったため。あわせて、この区間のまま次のVS画面が確定した場合
(=結果バナーを確定できずに試合を取りこぼした場合)はWARNINGを出す。従来は
Issue #243のINFOログしか出ておらず、8試合ぶんのデータが無言で消えていた。
main.pyはmatch_end_seenプロパティを見て、同じ区間のフレームを静止画として
保存する(banner_debug_frames.py参照)。いずれも閾値の再較正が済むまでの調査用

Issue #176: 降格(帯番号-1)は、昇格(is_league_change_screen()の全画面
オーバーレイ)と違って独立した確認手段が無く、_infer_tier_after()(当時の
_infer_tier_from_gauge_continuity())の
「負けているのにゲージ小数部が閾値を超えて増えて見える」という間接的な
推測に頼っていた。調査の結果、降格時にランクバッジ上へ乗る「降格」ラベル
(白背景の吹き出し)が、Issue #73で断念したS/A帯バッジのOCRとは異なり
形状(輝度)・OCRいずれの手法でも安定して検知できることが分かったため
(detection/league_change.pyのモジュールdocstring参照)、
is_demotion_label_candidate()/confirm_demotion_label_text()を追加した。
match_end/goalと同じ2段構成(色/形状の軽量な候補判定→デバウンス確定時に
1回だけOCRで確認)を、TRACKING_RANK中(_track_rank())で常時チェックする形で
組み込み、確認できれば_demotion_confirmed_this_matchに保持する。
_infer_tier_after()では、この独立信号が得られていれば
ゲージ小数部の閾値判定より優先して降格と確定させ、得られていない場合は
従来どおりの間接的な推測にフォールバックする(見逃しても既存の正しさは
損なわれない、という他の2段構成の信号と同じ設計)。

Issue #202: 上記の実装直後、降格ラベルを確認できていても帯番号OCRが
「変化なし」(delta=0)を返した場合には独立信号が一切参照されず、降格が
記録から漏れる穴が見つかった。当時の_is_tier_change_plausible()がdelta=0を
無条件に許容していたため、推測経路へのフォールバック自体が発生しなかったことが
原因。負け試合かつ_demotion_confirmed_this_matchがTrueの場合はdelta=0も
不自然とみなすよう修正して対応した。Issue #396でGRACE中の帯番号OCR自体を
廃止し、帯番号は常に独立信号のみで決めるようになったため、この穴は構造的に
発生しなくなった(該当のチェック自体も削除済み)。

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

対策として、VS画面確定を検知した瞬間(`_vs_screen_confirm_seconds`のデバウンス
成立時)に`self._in_match = True`・`self._session_match_no`のインクリメント・
「試合開始」ログを即座に行い、`read_vs_screen_ranks()`/`read_team_colors()`は
`_run_vs_screen_ocr()`として切り出した。この試合が完全に終わる(`_finalize()`)までは
`_vs_recorded_this_match`がTrueのままなので、同じ試合中に次のVS画面OCRが重ねて
走ることはない。

Issue #397: 当初はこの切り出し先をバックグラウンド**スレッド**にしていたが、
PaddleOCRの推論中はGILが解放されない(Issue #303で判明済みの制約)ため、
スレッドでもメインループが道連れで止まっていた。実配信ログの実測で、この処理中に
検知ループが4.4〜4.8秒フレームを1枚も評価しない区間が生じており、
「試合開始直後」の停止117回・合計401.5秒の主因になっていた(#383のコメント参照)。
`_rank_ocr_executor`(本番は`ProcessPoolExecutor`)へ投げる形に変更し、結果は
`_poll_vs_ocr()`がメインスレッド側で毎フレーム非ブロッキングに取り込む。
`_finalize()`はpendingフィールドを`MatchResult`に積む前に`_poll_vs_ocr(wait=True)`で
完了を待つ(通常はOCR自体が最大16秒・試合は数分続くため待たされることはないが、
念のための安全策)。結果の取り込みがメインスレッドに集約されたため、
`_vs_screen_event`の並行アクセス保護(旧`_vs_screen_event_lock`)は不要になった。

結果バナー確定時のランクバッジ読み取り(`_run_rank_before_ocr()`/`_apply_rank_before_ocr()`、コンパクト/拡大の
2回で実測約2.3秒)も同じ理由で`_run_rank_before_ocr()`にまとめ、同じExecutorへ
投げるようにした。こちらは`rank_before`が決まらないとTRACKING_RANKへ進めないため
投入直後に完了を待つが、待っている間はGILが解放されるので`FfmpegFrameReader`の
読み取りスレッドはフレームを取り込み続けられる(#398で暗転判定を読み取り側へ
移すと、この区間の暗転も取りこぼさなくなる)。VS画面OCRとrank_before OCRは
同じ試合の中で「試合開始時」「試合終了時」に分かれて走り決して同時には走らない
ため、ワーカープロセスを1つ共有している。

Issue #430: 上記の「投入直後に完了を待つ」をやめ、投げたらすぐTRACKING_RANKへ
進むようにした。待っている間(実測2.4〜4.0秒、#397時点の2.3秒より長い)は
読み取りスレッドこそ止まらないものの、メインループはフレームを1枚も評価しない。
2026-09-11の配信では、この間にランク変動アニメーション(約3秒)が丸ごと終わって
いたため、ゲージ追跡(`rank_after_ocr`が出ない/ワイプ中の値を拾う)・降格ラベル
検知・手動入力用クリップ(0.1〜6.8秒しか残らない)のすべてがアニメーションを
取りこぼしていた。結果は`_poll_rank_before_ocr()`が毎フレーム非ブロッキングに
取り込み、帯番号の起点(`_grace_candidate_rank_tier`)もその時点で埋める。
結果が無いと先へ進めない場面(ランクを賭けない試合の即時確定・暗転での確定・
`_finalize()`)でだけ`wait=True`で完了を待つ。暗転での確定時に待つのは、暗転が
既に過ぎているため待っても取りこぼすものが無いから(待たずに素通りすると帯番号の
起点が無いままGRACE満了まで確定が延びる)。

Issue #303 → #396: TRACKING_RANK(GRACEフェーズ)中の帯番号定期再チェックは
**廃止した**。経緯は以下のとおり。

`_track_rank()`はかつて`rank_recheck_interval_seconds`おきに`read_rank()`で帯番号を
読み直していたが、Issue #288で`read_rank()`をEasyOCR→PaddleOCRに変更したことで
1回あたり約1.2〜1.6秒(実測)かかるようになり、`process_frame()`全体が実時間で
最大約28秒ブロックされる事象が2026-08-09の実機動画・ログで確認された。
当初はバックグラウンドスレッドに逃がす対策を試したが、PythonのGILがPaddleOCRの
推論中(CPUバウンドなネイティブ計算)は1秒以上連続で解放されないことが実測で
判明したため、Issue #303では`ProcessPoolExecutor`経由で別プロセスへ逃がしていた。

Issue #396でこの定期再チェックごと、GRACE中の帯番号OCRをすべて廃止した。
2026-09-04・09-06の実配信3セッションの解析で、この区間の同期OCRと別プロセスへの
フレーム受け渡し・CPU競合が「試合終了+10〜19秒(=暗転が現れる区間)」の
検知ループ停止61回・合計92.5秒の主因になっており、0.40秒しかない暗転を
取りこぼす原因(#383)になっていることが分かったため。

現在の帯番号(整数部)は、結果バナー確定時に読み取った試合前の帯番号
(`_pending_rank_before_tier`)を起点に、昇格演出(`is_league_change_screen`)・
降格ラベル(`confirm_demotion_label_text`)という**帯番号OCRとは独立した信号**でのみ
±1する(`_infer_tier_after()`)。小数部はHSVベースで軽量な`read_rank_gauge_fill()`を
デバウンスした`_latest_gauge_fill`。GRACE中に走る重い処理は無くなった。

これに伴い、帯番号OCRの誤読を前提にしていた再スキャン経路(`_begin_finalize()` /
`_continue_rescan_wait()` / `_is_tier_change_plausible()` / `_RankPhase.RESCAN_WAIT` /
`_fill_grace_candidate_if_missing()`)も削除した。読まなくなった値の妥当性を
検証する必要が無いため。Issue #136で作った推測規則自体は
`_infer_tier_from_gauge_continuity()`→`_infer_tier_after()`として残っており、
「帯番号OCRが壊れた時のフォールバック」から「帯番号を決める唯一の方法」へ
格上げされた形になる。

この変更で`rank_after_ocr`(手動入力ページに出る参考値)の帯番号は、OCRの実測値
ではなく上記の推測値になる。DBに最終的に残る`rank_after`・`league_changed`は
`/rank-entry`の手動入力(`db.save_manual_rank_after()`がrank_beforeとの帯比較で
league_changedを再計算する)が上書きするため、記録の正しさは損なわれない
(ユーザーと合意済み)。

Issue #327: Issue #303と同じ種類の不具合が、ゴール検知(`_check_for_goal`)側でも
実配信のテストで見つかった。得点者名パネルのラベル確認(`confirm_goal_text`)・
得点者名(`read_scorer_name`)・アシスト名(`read_assist_name`)・オウンゴール判定
(`is_own_goal_event`)を毎回同期的に呼んでおり、1回のゴール検知で最大6回程度
PaddleOCRを直列に呼ぶため、実測で数秒メインループをブロックしうる。ゴールは
試合終了の直前に発生することも多く、このブロックが直後の結果バナー確定・
暗転検知(`/rank-entry`用クリップの録画終了トリガー)の遅延につながり、
本来は数秒〜十数秒で終わるはずのクリップ録画がほぼ毎回`MAX_DURATION_SECONDS`
(`rank_entry_clips.py`、60秒)の安全策に張り付く事象として顕在化した。

対策は`_run_goal_ocr()`として上記4関数の呼び出しを1つの関数にまとめ、
`goal_ocr_executor`(`tier_recheck_executor`と同じ設計、本番はmain.pyが
別の`ProcessPoolExecutor`を渡す。1つのワーカーを共有すると片方のOCRがもう
片方を待たされてしまうため、tier_recheck_executorとは別プロセスにする)へ
丸ごと投げる。`_run_goal_ocr()`をモジュール直下の関数として定義しているのは、
(1)`ProcessPoolExecutor`がpickle参照のため通常のメソッド・クロージャにできない、
(2)内部で呼ぶ`confirm_goal_text`等をこのモジュールの名前空間で解決させることで、
既存テストの`monkeypatch.setattr(match_state_module, "confirm_goal_text", ...)`が
そのまま効くようにするため(`detection.goal`側に置くとテストの差し替えが
効かなくなる)。

帯番号の定期再チェック(Issue #303)とは異なり、ゴール自体は取りこぼすと
そのゴール1件が二度と記録されない(次の試合の`_pending_goals`に紛れ込ませて
しまうのはさらに悪い)。そのため`_finalize()`は`_poll_vs_ocr(wait=True)`と同じ
考え方で、`_goal_ocr_future`が残っていれば完了を待ってから`_pending_goals`を
`MatchResult`へ積む(`_poll_goal_ocr(wait=True)`)。通常時(`wait=False`)は
`process_frame()`から状態に関わらず毎フレーム呼び、ブロックせずに結果が
届いていれば取り込む。

Issue #324/#325(/rank-entryの動画コントロール修正)の作業自体はこの遅延の
原因ではないと切り分け済み(Issue #312で追加されたゲージ動画の毎フレーム処理は
録画開始=`tracking_rank`突入後にしか走らないが、今回の遅延は`watching`状態中の
結果バナー確定そのもので発生していた)。

Issue #224: 試合終了時のOBSシーン切替(in_match=False)が、結果画面から離脱する
フェード演出とタイミングが重なり、体感上早すぎるタイミングで発生する事象が
実配信で確認された(Issue #223の調査中に発見)。実測(is_full_blackoutの
輝度チェック)では、この時点で画面全体はまだ暗転(輝度平均≤15かつ標準偏差≤15)
していない(バッジ周辺が局所的に暗くなっているだけで、全画面平均では
輝度40前後)ことが分かっており、暗転誤検知が原因ではなく、「試合終了」バナー
消灯+ゲージ変化なし0.5秒の早期確定パス(Issue #178)が、このフェード演出と
たまたま重なっていることが原因と判明した。

対策として、OBSシーン切替のタイミングを、ランク値(rank_after)の確定タイミング
(これまでどおり: バナー消灯+ゲージ変化なし0.5秒、通常のgrace期間満了、または
暗転による即時確定のいずれか)から切り離した。`_finalize()`は「試合終了」確認済み
なら`_pending_obs_switch`フラグを立てるだけにとどめ、実際に`in_match`をFalseに
戻すのは`_check_pending_obs_switch()`が毎フレーム(状態に関わらず)監視し、
`is_full_blackout()`を最初に検知してから`obs_switch_delay_after_blackout_seconds`
(既定30フレーム=1秒相当)経過した時点で行う。暗転自体の実測継続時間は
0.3〜0.5秒程度(detection.motion.is_full_blackoutのモジュールdocstring参照)と
1秒より短いため、「暗転が続いている間だけ数える」のではなく、最初に検知した
瞬間からの単純な経過フレーム数で数える(暗転が終わってマッチング画面に戻っても
タイマーはリセットしない)。ランク値の記録タイミング自体は変更していない。

`is_full_blackout()`が何らかの理由で一度も発火しなかった場合、in_matchが
切り替わらないまま次の試合に持ち越される。fixtures/videos全24本の実測では
暗転は必ず現れることを確認済み(モジュールdocstring内Issue #209参照)なため
許容する(Issue #190の「「試合終了」を確認できなかった場合の持ち越し」と
同じ性質の劣化パターン)。次の試合のVS画面確定が先に来た場合(暗転待ちの
間に次の試合が始まる、実際にはほぼ起きないはずのレアケース)は、in_match=True
を優先し`_pending_obs_switch`を破棄する(`_check_for_vs_screen()`参照)。

上記の対応後、ユーザーから追加の仕様確認があった: このゲームでは試合終了後、
「ランク変更→暗転1→別画面→暗転2→マッチング画面」の順で**暗転が2回**現れる
(試合開始前にも別途1回現れるが、これは`_pending_obs_switch`がFalseの間は
`_check_pending_obs_switch()`が素通りするため無関係)。切替のトリガーに
使うべきは常に暗転1のみで、暗転2(別画面を挟んだ2回目)は無視したい、という
要件だった。

`_check_pending_obs_switch(frame)`の呼び出し位置が`process_frame()`の先頭
だと、この要件を満たせないケースがあった。`_finalize()`は複数の経路から
呼ばれるが、そのうちIssue #209の「暗転を検知したら即確定」パスでは、確定の
トリガーそのものが暗転1のフレームである。この経路で`_pending_obs_switch`が
Trueになるのは`process_frame()`呼び出しの途中(状態振り分け先の`_track_rank()`
内)のため、状態振り分けより前で`_check_pending_obs_switch()`を呼ぶと、まだ
`_pending_obs_switch`がFalseのまま素通りしてしまい、「このフレーム自体が
暗転1だ」と気づけない。次のフレームで改めて`is_full_blackout()`を待つ形に
なるが、暗転1は既に終わっている(実測0.3〜0.5秒)ため、次に検知するのは
別画面を挟んだ暗転2になってしまう。

対策として、`_check_pending_obs_switch(frame)`の呼び出し位置を状態振り分けの
**後**に移動した。これにより、`_finalize()`が暗転1のフレームそのものを
トリガーに呼ばれた場合でも、同じフレームで`is_full_blackout(frame)`を
再チェックしてすぐカウンターを開始できる。一度カウンターが始まったら、
その後は`is_full_blackout()`を再チェックせず単純に経過フレーム数だけを
数えて発火するため、途中で暗転2が来ても(あるいは来なくても)無視される。
「暗転1と暗転2の間に試合終了系の検知を一切行っていない暗転は無視する」
という要件は、この「暗転1を正しく捕まえたら、以降は数えるだけ」という
設計で自然に満たされる。

なお、`_finalize()`がgrace期間満了(league_change_grace_seconds、既定5秒)
経由で暗転1より後に呼ばれてしまう極端なケース(ゲージの緩やかな変動で
スタビリティ判定が長引く等)は、この修正の範囲外の既知の弱点として残って
いる。この場合`_check_pending_obs_switch()`が次に検知する暗転は暗転2(場合
によってはさらに後の、次の試合の試合開始前の暗転)になってしまう可能性が
あり、OBSシーン切替が意図したタイミングより遅れる(または全く違うタイミング
になる)。ユーザーと相談の上、今回は主要な経路(暗転即時確定パス)のみ対応
することとし、この弱点への対応は見送った(→ Issue #371で対応済み、下記参照)。

Issue #371: 上記で見送った弱点が、実配信(2026-08-14、10試合)で無視できない
頻度・規模で表面化した。10試合中3試合でOBSシーン切替が大幅に遅延しており
(実測: 試合終了の27.9秒後・123.8秒後・246.6秒後)、特に後者2件は次の試合が
始まる直前まで試合中シーンのままだった。原因は上記の弱点そのもので、
`_finalize()`がGRACEフェーズの不安定リセット(下記「_track_rank」参照)で
20〜36秒遅れ、その時点では暗転1どころか暗転2も過ぎ去っていたため、
`_check_pending_obs_switch()`が拾えるのはさらに後の無関係な暗転になっていた。

対策として、`_pending_obs_switch`を立てる場所を`_finalize()`から
`_check_for_match_end()`(「試合終了」バナーのOCR確認`confirm_match_end_text`が
成功した瞬間)へ前倒しした。「試合終了」の確認はランクバッジの読み取り可否・
GRACEの長さに一切依存せず毎回安定して成功しているため、以降どれだけランク確定が
長引いても暗転1を取りこぼさない。暗転の判定基準(`is_full_blackout`)・暗転1を
掴んだ後は数えるだけという仕組み自体は変更していない(監視を開始する位置だけを
早めた)。Issue #190の「「試合終了」を確認できた試合に限り切り替える」という
ゲート自体も、フラグを立てる条件がまさにその確認そのものになるため維持される。

あわせて`obs_switch_delay_after_blackout_seconds`を1秒→5秒に延長した。実配信の
録画を`is_full_blackout`相当の計算で走査したところ、試合終了後の暗転は
以下の並びであることが分かったため:

- 通常の試合: 暗転1が試合終了の**7〜9秒後**(結果バナー・ランク変動の直後)、
  暗転2が**13〜15秒後**(tips画面「いまのラッキースポーツは…」等を挟んだ後)
- 昇格した試合: 暗転は**1回のみ**(13.5秒後)。昇格演出が結果画面の区間に
  入るため、通常の試合でtips画面が入る区間ごと置き換わり暗転1に相当するものが
  現れない

修正前は(上記のとおり`_finalize()`がGRACE満了まで待つ関係で)正常に見えていた
試合も実際には暗転2で切り替わっており、実測の切替タイミングは試合終了の
15〜17秒後だった。起点を暗転1へ前倒しすると、遅延を1秒のままにした場合は
8〜10秒後となり従来より6〜7秒早くなってしまう。ユーザーと相談し、tips画面が
配信に映る時間を従来どおり残したいという理由で、遅延を5秒にして切替を
12〜14秒後に着地させることにした(昇格した試合は暗転が1回だけのため
18.5秒後となり従来より約3秒遅くなるが、許容範囲として合意済み)。

Issue #395: 上記の前倒し(#371)を入れてもなお、暗転そのものを取りこぼす事象が
残っている(原因は検知ループが1.3〜4.8秒単位で止まり、その間フレームを1枚も
評価していないこと。#383のコメント参照)。実配信3セッション25試合の実測では
12試合で切替が遅延し、最大258.5秒だった。根本対策(#396〜#398)が入るまでの
安全網として、`_check_pending_obs_switch()`に`obs_switch_timeout_seconds`
(既定30秒)を設け、「試合終了」OCR確認からこの秒数が経過しても暗転を一度も
検知できていなければ暗転を待たずに切り替える。

30秒という値は、正常に暗転を検知できた13試合の「試合終了→切替」が14.7〜25.8秒
だったことによる。これより短くすると正常経路を先回りして切ってしまい、tips画面が
配信に映る時間(上記#371で意図して確保したもの)が変わってしまう。

この経路を通った場合は必ずWARNINGログを残す。安全網は症状を隠す対策であり、
隠したことがログから見えないと、根本対策を入れた後に「暗転の取りこぼしが実際に
減ったのか」を測る手段が無くなるため。

Issue #398: 上記の安全網(#395)とは別に、暗転そのものを取りこぼさないための
根本対策として、暗転の判定を`capture.FfmpegFrameReader`の読み取りスレッド側へ
移せるようにした。`read()`は「その時点の最新フレーム」しか返さないため、
検知ループが重い処理で止まっている間に届いたフレームは読み捨てられる。
0.40秒(60fpsで24〜25枚)しかない暗転はここで丸ごと失われうる。

`detection.motion.BlackoutWatcher`が読み取りスレッドから全フレームを観測し、
`process_frame(frame, blackout)`の第2引数として「前回の呼び出し以降に暗転を
観測したか・その区間の最小輝度」を受け取る。渡された場合は暗転の判定に
このフレーム単体ではなく観測結果を使う(`_check_pending_obs_switch()`・
`_track_rank()`の暗転即時確定パスの両方)。省略時は従来どおりこのフレーム単体を
`is_full_blackout()`で判定するため、既存のテスト・呼び出しはそのまま動く。

Issue #222: 結果バナー確定直後の`rank_before`読み取り(`_watch_for_banner()`)が、
負け試合を中心に`None`(読み取り失敗)になる不具合を調査した。バナー確定直後は
「まだコンパクト表示のはず」という前提で`GAUGE_ROI_COMPACT`/`RANK_NUMBER_ROI_COMPACT`
のみを使っていたが、実データ(2026-07-31の実機テストセッション)を確認したところ、
ランクバッジがコンパクト→拡大表示へアニメーションで切り替わるタイミングと、
バナー色判定の確定タイミングが競合し、確定した頃には既に拡大表示へ切り替わって
いるケースがあった。

当初「勝ちバナーより負けバナーの方が色判定の確定に時間がかかる」という仮説を
立てたが、fixtures/videosのクリーンな動画(win 9本・lose 6本)と実機録画の
両方で検証したところ、**バナーが実際に画面へ出てから確定するまでの時間は
勝ち負けで差が無い(いずれも約0.97〜0.98秒)ことが分かり、この仮説は否定された**。
確定処理自体の速度ではなく、バッジ自体がコンパクト表示に留まる時間(あるいは
バッジがいつ画面に現れるか)が試合ごとに変動しており、それが確定タイミングとの
競合を引き起こしていると考えられるが、根本的な原因(ゲーム側の演出タイミングの
性質)はライブキャプチャのフレーム抜け等のノイズもあり完全には特定できていない。

原因の完全特定を待たず、`_run_rank_before_ocr()`/`_apply_rank_before_ocr()`で対策した: `GAUGE_ROI_COMPACT`/
`RANK_NUMBER_ROI_COMPACT`と`GAUGE_ROI_ENLARGED`/`RANK_NUMBER_ROI_ENLARGED`の
両方で`read_precise_rank()`を試し、読み取れた方(`None`でない方)を採用する。
間違ったROI(コンパクト表示にENLARGED、拡大表示にCOMPACT)を当てた場合は常に
`None`を返すことをfixtures/screenshots 4件の実データで確認済みのため、通常は
どちらか一方だけが成功し、「両方成功して異なる値を返す」リスクは低いと判断した
(遷移アニメーション中の中間状態のフレームでは未検証のため、その場合に限り
コンパクト側を優先しWARNINGログに残す)。この対策は原因(なぜコンパクト/拡大の
どちらになるか)を問わず両方のケースに対応できるため、根本原因の特定より先に
着手した。実機での効果検証は別途行う。

Issue #229: 2026-08-04の実機テストセッションで、3試合目終了直後に、ランク変動が
わずか0.03しか無い「勝ち」の結果バナーが再度誤検知され、実際には存在しない
4試合目としてDBに記録される不具合が確認された。VS画面・チームカラーはどちらも
検知できておらず、直前の3試合目の残像(暗転〜マッチング画面手前のどこかの画面)を
誤って結果バナーとして拾ったとみられる。

当時のIssue #39は「VS画面検知は任意のエンリッチであり、見逃しても既存の結果
バナー起点フローには影響させない」という方針だったが、VS画面検知の精度は
その後の実データで99%程度信頼できる水準まで改善しているとユーザーから確認が
取れたため、この方針を転換し、**試合の区切りをVS画面確定に一本化する**ことにした。

`_watch_for_banner()`の結果バナー確定処理に、`_vs_confirmed_this_match`(この
試合でVS画面を一度でも確認できたか)のチェックを追加した。これがFalseの状態で
結果バナーが確定した場合、新しい試合としては記録せず(MatchResultを作らない、
`_session_match_no`も増やさない)、直前の試合番号のままINFOログに「誤検知として
スキップした」ことを出す。このバナーに紐づいてバッファされている可能性のある
ゴール検知(`_pending_goals`)も、次の本物の試合に誤って持ち越さないよう
あわせて破棄する。

`_vs_confirmed_this_match`は、既存の`_vs_recorded_this_match`(VS画面OCRの
重複発火を防ぐための短命なフラグ。`_check_for_vs_screen()`でis_vs_screenが
Falseに戻った瞬間、つまりVS画面が視覚的に消えて数フレーム後には早々にFalseへ
戻ってしまう)とは別の、新設したフラグである。VS画面確定でTrueにし、
`_finalize()`でのみFalseに戻す(試合の終わりまで確認結果を覚えておく必要が
あるため、既存のvs_recorded_this_matchを再利用すると、結果バナー確定時点
(試合開始から数分後)には既にFalseに戻ってしまっていて使えない)。

この変更はIssue #39の既存方針を正面から覆すものであり、既知のトレードオフとして
以下を許容する(ユーザー確認済み):

- VS画面を本当に見逃した試合(検知精度が上がったとはいえ、残り僅かなケースで
  見逃す可能性はゼロではない)は、勝敗・ランクを含め試合結果ごと記録されなくなる
- アプリを試合の途中から起動した場合(その試合のVS画面を確実に見逃す)も、
  その試合は記録されなくなる
"""

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from logging import getLogger
from typing import Callable, NamedTuple, Optional

import numpy as np

from nss_tracker.config import get_goal_record_mode, is_allowed_player
from nss_tracker.detection.banner import BannerResult, banner_roi_stats, classify_banner
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
from nss_tracker.detection.matchmaking import is_vs_screen
from nss_tracker.detection.motion import (
    BlackoutObservation,
    StabilityMonitor,
    frame_brightness_stats,
    is_full_blackout,
)
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    RANK_NUMBER_ROI_COMPACT,
    RANK_NUMBER_ROI_ENLARGED,
    RANK_ROI,
    read_precise_rank,
    read_rank_gauge_fill,
)
from nss_tracker.detection.team_color import read_team_colors
from nss_tracker.detection.vs_rank import SlotRank, read_vs_screen_ranks
from nss_tracker.detection_config import get_detection_value
from nss_tracker.timeutil import now_jst

logger = getLogger("nss_tracker.state")

# Issue #71: 勝敗結果ログ用の表示文言
_BANNER_RESULT_LABELS = {"win": "勝ち", "lose": "負け", "draw": "引き分け"}

# Issue #388: 以下のDEFAULT_XXX_SECONDSはすべてconfig/detection.tomlの
# [match_state]で上書き可能。以前はフレーム数(30fps想定)で持っていたが、
# 検知ループの実効fpsが変動しても閾値の意味(秒数)が変わらないよう実時間ベースに
# 変更した(モジュールdocstring参照)。

# Issue #67: 通常プレイ中の背景誤検知(1.3秒程度持続)を確実に防ぐため、
# 他のconfirm系(1秒)より長い2秒をデフォルト値にしている
DEFAULT_BANNER_CONFIRM_SECONDS = get_detection_value("match_state", "BANNER_CONFIRM_SECONDS", 2.0)
# Issue #76: 「試合終了」バナーを確認できている場合のbanner_confirm_seconds。
# Issue #67修正前のデフォルト(1秒)と同じ値に戻す(本当の試合終了直前だと
# 分かっているため、通常プレイ中の背景誤検知を心配する必要が無い)
DEFAULT_BANNER_CONFIRM_SECONDS_AFTER_MATCH_END = get_detection_value(
    "match_state", "BANNER_CONFIRM_SECONDS_AFTER_MATCH_END", 1.0
)
DEFAULT_BANNER_ABSENCE_CONFIRM_SECONDS = get_detection_value("match_state", "BANNER_ABSENCE_CONFIRM_SECONDS", 1.0)
DEFAULT_GOAL_CONFIRM_SECONDS = get_detection_value("match_state", "GOAL_CONFIRM_SECONDS", 1.0)
DEFAULT_VS_SCREEN_CONFIRM_SECONDS = get_detection_value("match_state", "VS_SCREEN_CONFIRM_SECONDS", 1.0)
# Issue #234: VS画面確定直後、演出中(キック演出のスウォッシュ等)の一瞬の
# is_vs_screen判定揺れによって同じVS画面のまま試合開始が二重に発火する不具合が
# 実データで見つかった。確定後はこの秒数、新規のVS画面検知自体を行わないことで
# 対症療法的に防ぐ
VS_SCREEN_LOCKOUT_SECONDS = get_detection_value("match_state", "VS_SCREEN_LOCKOUT_SECONDS", 30.0)
# Issue #176: 降格ラベルは実測で2秒以上安定して表示され続けるため(detection/
# league_change.pyのモジュールdocstring参照)、goal/vs_screenと同じ1秒で良い
DEFAULT_DEMOTION_LABEL_CONFIRM_SECONDS = get_detection_value("match_state", "DEMOTION_LABEL_CONFIRM_SECONDS", 1.0)
# Issue #190: このデバウンスは誤検知を防ぐ安全マージンとしては機能しておらず
# (実際の真偽はOCR文字一致confirm_match_end_textが決める)、「試合終了」バナーは
# 実データで最短7フレーム程度(60fps)しか綺麗に表示されないケースがあったため、
# 色候補判定を満たした最初のフレームで即OCR確認する(0.0秒=即時、モジュール
# docstring参照)
DEFAULT_MATCH_END_CONFIRM_SECONDS = get_detection_value("match_state", "MATCH_END_CONFIRM_SECONDS", 0.0)
# 実測(fixtures/videos/01_win_blue_2-1.mp4, 60fps):
# ランク数値が一旦静止してから昇格演出が始まるまで約270フレーム(60fpsで4.5秒)の間があった
DEFAULT_LEAGUE_CHANGE_GRACE_SECONDS = get_detection_value("match_state", "LEAGUE_CHANGE_GRACE_SECONDS", 5.0)
# GRACE中にゲージの緩やかな変化を見逃さないよう再読み取りする間隔(秒)
DEFAULT_RANK_RECHECK_INTERVAL_SECONDS = get_detection_value("match_state", "RANK_RECHECK_INTERVAL_SECONDS", 0.5)
# 再読み取りで「値が変わった」とみなす閾値。ゲージ読み取り自体の測定誤差
# (tests/test_rank_ocr.pyでabs=0.02を許容)より大きく取り、ノイズで
# 猶予期間を無駄に延長し続けないようにする
RANK_RECHECK_CHANGE_TOLERANCE = get_detection_value("match_state", "RANK_RECHECK_CHANGE_TOLERANCE", 0.05)
# Issue #423: 「試合終了」確認後のバナーROI実測値ログ(_log_banner_stats)の間引き幅。
# H/S/Vのいずれかがこの値を超えて動いたときだけログに出す。本物のバナーが出ている間の
# 実測はフレーム間でほぼ完全に一定(実測でH/S/Vとも小数第2位まで変化しない)なため、
# 1.0でも「バナーが出た/消えた」等の意味のある変化は取りこぼさない
_BANNER_STATS_LOG_TOLERANCE = 1.0

# Issue #136: 試合前後で帯番号(整数)が2以上急変した場合の再スキャンまでの
# 待機秒数。同一フレームへの再OCRは同じ誤読を繰り返すだけのため、少し時間を
# 置いた別フレームで読み直す

# Issue #136: 再スキャンしても帯番号が不自然なまま(1帯を超える変化、または
# 昇格演出未確認の+1、または勝敗と矛盾する向きの変化)だった場合、ゲージ小数部
# (HSVベースの独立信号)の連続性で判断し直す。「勝ったら降格しない/負けたら
# 昇格しない」というゲーム仕様(ユーザー確認済み)を前提に、勝敗と矛盾する
# 向きにこの割合を超えて動いて見える場合にのみ1帯またいだとみなす
RANK_TIER_WRAP_MIN_MAGNITUDE = get_detection_value("match_state", "RANK_TIER_WRAP_MIN_MAGNITUDE", 0.5)

# Issue #224: 試合終了時のOBSシーン切替(in_match=False)は、ランク値の確定
# タイミングとは切り離し、「暗転(is_full_blackout)を最初に検知してからこの
# 秒数経過後」に統一する。暗転自体の実測継続時間は0.3〜0.5秒程度
# (detection.motion.is_full_blackoutのモジュールdocstring参照)とこの値より
# 短いため、暗転が続いている間だけ数えるのではなく、最初に検知した瞬間からの
# 単純な経過秒数で数える(モジュールdocstring参照)。
# Issue #371: 1秒から5秒へ延長した。切替の起点が暗転2から暗転1へ前倒しになった分、
# そのままでは体感の切替タイミングが6〜7秒早まってしまうため(実測値の根拠は
# モジュールdocstring参照、ユーザーとの相談で決定)
DEFAULT_OBS_SWITCH_DELAY_AFTER_BLACKOUT_SECONDS = get_detection_value(
    "match_state", "OBS_SWITCH_DELAY_AFTER_BLACKOUT_SECONDS", 5.0
)

# Issue #395: 暗転自体を取りこぼした場合の安全網。「試合終了」OCR確認から
# この秒数が経過しても暗転を一度も検知できていなければ、暗転を待たずに
# in_matchをFalseへ戻す(モジュールdocstring参照)。
# 2026-09-04・09-06の実配信3セッション25試合の実測では、正常に暗転を検知できた
# 13試合の「試合終了→切替」は14.7〜25.8秒(暗転を検知したのは最も遅い試合で
# 試合終了の20.8秒後、そこからOBS_SWITCH_DELAY_AFTER_BLACKOUT_SECONDS=5秒)。
# これより短い値にすると正常経路を先回りして切ってしまい、tips画面の見え方が
# 変わってしまうため、26秒より余裕を持たせた30秒とした(ユーザーとの相談で決定)
DEFAULT_OBS_SWITCH_TIMEOUT_SECONDS = get_detection_value(
    "match_state", "OBS_SWITCH_TIMEOUT_SECONDS", 30.0
)


class _Debounce:
    """値が一定時間以上連続して陽性だったかを実時間ベースで判定する(Issue #388)。

    フレーム数ではなくnow(呼び出し元がtime.monotonic()系のクロックから渡す)で
    経過秒数を測ることで、検知ループの実効fpsが変動しても閾値の意味(秒数)が
    変わらないようにする(モジュールdocstring参照)。「同じ値が持続しているか」の
    判定に使う(banner確定・goal確定・vs_screen確定・match_end確定・降格ラベル
    確定・ゲージ塗りつぶしの許容誤差内デバウンス)。値そのものの追跡(banner候補や
    ゲージ値そのもの)は呼び出し側の責務とし、このクラスは「陽性/陰性」の
    ブール値のみを扱う。

    ストリークの起点(_started_at)は、陽性に転じたそのフレーム自身ではなく
    直前に観測した(陰性だった)フレームの時刻を使う。これは「実際に陽性へ
    転じたタイミングは直前の観測より後、今回の観測以前のどこか」という
    最も保守的な下限の見積もりであり、fixture動画で判明した実害を防ぐために
    必要: 一定fpsでサンプリングされた実映像では、ある状態がちょうどN個の
    連続フレームぶん映る場合、その実時間の長さはフレーム間隔×N(各フレームが
    1/fps秒分の時間を占めるとみなす)だが、フレーム自身の時刻の差分(1個目から
    N個目まで)はフレーム間隔×(N-1)にしかならない。起点をそのフレーム自身に
    すると実時間を後者(1フレーム分短く)しか測れず、旧来のフレーム数閾値と
    ちょうど同じ長さしか映らなかった実データ(fixtures/videos/
    33_lose_pink_overtime_hdr_off.mp4のVS画面)で確定を取りこぼす回帰があった。
    直前の観測フレームの時刻を起点にすることで、この1フレーム分のずれを
    解消する。ただしこのデバウンス自身が生成されて以降、一度も陰性の観測が
    無いまま最初から陽性が続いている場合(直前の観測が存在しない)は、今回の
    フレームを起点にするしかない。
    """

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self.streak = 0
        self._started_at: Optional[float] = None
        self._last_observed_at: Optional[float] = None

    def observe(self, positive: bool, now: float) -> bool:
        """今回のフレームが陽性かどうかを渡し、閾値(seconds)に到達したかを返す。"""
        if not positive:
            self.reset()
            self._last_observed_at = now
            return False
        self.streak += 1
        if self._started_at is None:
            self._started_at = self._last_observed_at if self._last_observed_at is not None else now
        self._last_observed_at = now
        return (now - self._started_at) >= self._seconds

    def reset(self) -> None:
        self.streak = 0
        self._started_at = None
        # 呼び出し元(_watch_for_banner・_finalize等)が試合の区切りで明示的に
        # reset()する場合も含め、古い試合の時刻を次の試合のストリーク起点に
        # 誤って引き継がないようクリアする(observe(positive=False, ...)から
        # 呼ばれた場合はこの直後にそちらがnowで上書きする)
        self._last_observed_at = None


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


@dataclass
class GoalEvent:
    scorer_name: Optional[str]
    assist_name: Optional[str]
    detected_at: datetime
    # Issue #217: オウンゴールかどうか(検知層が見たものをそのまま報告するのみで、
    # GOAL_RECORD_MODEに応じた記録可否の判断はdatabase.db.save_goal側の責務)
    is_own_goal: bool = False


class GoalOcrResult(NamedTuple):
    """`_run_goal_ocr()`(Issue #327)の戻り値。confirmedがFalseの場合は誤検知として
    扱い、他のフィールドは意味を持たない。
    """

    confirmed: bool
    scorer: Optional[tuple[str, float]]
    assist: Optional[tuple[str, float]]
    is_own_goal: bool


class VsScreenOcrResult(NamedTuple):
    """`_run_vs_screen_ocr()`(Issue #397)の戻り値。"""

    mine_ranks: list[SlotRank]
    opponent_ranks: list[SlotRank]
    mine_team_color: Optional[str]
    opponent_team_color: Optional[str]


def _run_vs_screen_ocr(frame: np.ndarray) -> VsScreenOcrResult:
    """VS画面のランクOCRとチームカラー読み取りをまとめて実行する(Issue #397)。

    Issue #189ではバックグラウンドスレッドに逃がしていたが、実測でこの処理中に
    検知ループが4.4〜4.8秒止まる(=フレームを1枚も評価しない)ことが実配信ログで
    分かった。PaddleOCRの推論中はGILが解放されないため、スレッドではメインスレッドも
    道連れで止まる(Issue #303で判明済みの制約)。_run_goal_ocr()と同じく
    ProcessPoolExecutorへ丸ごと投げられるよう、モジュール直下の関数にまとめた
    (モジュール直下に置く理由も_run_goal_ocr()と同じ)。
    """
    mine_ranks, opponent_ranks = read_vs_screen_ranks(frame)
    mine_team_color, opponent_team_color = read_team_colors(frame)
    return VsScreenOcrResult(
        mine_ranks=mine_ranks,
        opponent_ranks=opponent_ranks,
        mine_team_color=mine_team_color,
        opponent_team_color=opponent_team_color,
    )


class RankBeforeOcrResult(NamedTuple):
    """`_run_rank_before_ocr()`(Issue #397)の戻り値。どちらのROIで読めたかを
    呼び出し側(_apply_rank_before_ocr)が判断できるよう、両方をそのまま返す。
    """

    compact: Optional[tuple[int, float]]
    enlarged: Optional[tuple[int, float]]


def _run_rank_before_ocr(frame: np.ndarray) -> RankBeforeOcrResult:
    """結果バナー確定時点のランクバッジ読み取り(コンパクト/拡大の2回)を
    まとめて実行する(Issue #397)。

    Issue #222の経緯どおり、バナー確定時点でバッジがどちらの表示サイズかを
    事前に判定する手段が無いため2回試す必要がある。この2回で実測約2.3秒
    メインループが止まっていたため、_run_goal_ocr()と同じくProcessPoolExecutorへ
    丸ごと投げられるようモジュール直下の関数にまとめた。呼び出し側は結果を
    同期的に必要とする(rank_beforeが決まらないとTRACKING_RANKへ進めない)ため
    投入直後に完了を待つが、待っている間はGILが解放されるので
    FfmpegFrameReaderの読み取りスレッドはフレームを取り込み続けられる。
    """
    return RankBeforeOcrResult(
        compact=read_precise_rank(frame, GAUGE_ROI_COMPACT, RANK_NUMBER_ROI_COMPACT),
        enlarged=read_precise_rank(frame, GAUGE_ROI_ENLARGED, RANK_NUMBER_ROI_ENLARGED),
    )


def _run_goal_ocr(frame: np.ndarray) -> GoalOcrResult:
    """ゴール候補フレームに対するOCR一式(確認・得点者名・アシスト名・オウンゴール判定)を
    まとめて実行する(Issue #327)。

    以前は_check_for_goal()内でconfirm_goal_text/read_scorer_name/read_assist_name/
    is_own_goal_eventを個別に同期呼び出ししており、1回のゴール検知で最大6回程度の
    PaddleOCR呼び出しがメインループを直列にブロックしていた。Issue #303
    (帯番号の定期再チェック)と同じ理由・同じ対策として、この一式を1つの関数に
    まとめてgoal_ocr_executor(本番はProcessPoolExecutor)へ丸ごと投げられるようにした。
    モジュール直下の関数として定義しているのは、(1)ProcessPoolExecutorがpickle経由で
    参照するため通常のメソッド・クロージャにできない、(2)内部で呼ぶconfirm_goal_text等を
    このモジュールの名前空間で解決させることで、既存テストの
    `monkeypatch.setattr(match_state_module, "confirm_goal_text", ...)`が
    そのまま効くようにするため(detection.goal側に置くとテストの差し替えが効かなくなる)。
    """
    if not confirm_goal_text(frame):
        return GoalOcrResult(confirmed=False, scorer=None, assist=None, is_own_goal=False)
    return GoalOcrResult(
        confirmed=True,
        scorer=read_scorer_name(frame),
        assist=read_assist_name(frame),
        is_own_goal=is_own_goal_event(frame),
    )


class VsScreenEvent(NamedTuple):
    """VS画面を確定した直後の1フレームだけ`pop_vs_screen_event()`が返す読み取り結果(Issue #145)。

    フィールドの意味はMatchResultの同名フィールドと同じ。
    """

    mine_ranks: list[SlotRank]
    opponent_ranks: list[SlotRank]
    mine_team_color: Optional[str]
    opponent_team_color: Optional[str]
    # Issue #236: main.py側のログを試合中のリアルタイムログ(nss_tracker.state)と
    # 同じ「n試合目」表記に揃えるために追加。呼び出し元(main.py)はmatch_id
    # (DBの生ID)しか知らないため、セッション内の試合番号を別途持ち回る必要がある
    session_match_no: int = 0


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
    # Issue #236: VsScreenEventと同じ理由でセッション内の試合番号を持ち回る
    session_match_no: int = 0
    # Issue #374: 昇格演出(is_league_change_screen)/降格ラベル(is_demotion_label_candidate)を
    # 視覚的に検知できたかどうかを、rank_before/rank_afterの数値化成否とは独立に
    # 持ち回る。"up" / "down" / None。バッジが完全に読み取れずrank_before/rank_after
    # がどちらもNoneのまま記録される試合でも、帯が変化したこと自体は分かっている
    # ことがあるため、database.db側でrank_beforeチェーンの整合性チェックに使う
    # (通常はleague_changedと同じ内容になるが、数値ベースのleague_changedが
    # Noneになる場面でもこちらは独立して立ちうる)
    league_change_label_detected: Optional[str] = None


class MatchStateMachine:
    """フレームを1枚ずつ渡して試合結果を検知する状態機械。"""

    def __init__(
        self,
        rank_roi: tuple[int, int, int, int] = RANK_ROI,
        banner_confirm_seconds: float = DEFAULT_BANNER_CONFIRM_SECONDS,
        banner_confirm_seconds_after_match_end: float = DEFAULT_BANNER_CONFIRM_SECONDS_AFTER_MATCH_END,
        banner_absence_confirm_seconds: float = DEFAULT_BANNER_ABSENCE_CONFIRM_SECONDS,
        league_change_grace_seconds: float = DEFAULT_LEAGUE_CHANGE_GRACE_SECONDS,
        goal_confirm_seconds: float = DEFAULT_GOAL_CONFIRM_SECONDS,
        rank_recheck_interval_seconds: float = DEFAULT_RANK_RECHECK_INTERVAL_SECONDS,
        vs_screen_confirm_seconds: float = DEFAULT_VS_SCREEN_CONFIRM_SECONDS,
        vs_screen_lockout_seconds: float = VS_SCREEN_LOCKOUT_SECONDS,
        match_end_confirm_seconds: float = DEFAULT_MATCH_END_CONFIRM_SECONDS,
        demotion_label_confirm_seconds: float = DEFAULT_DEMOTION_LABEL_CONFIRM_SECONDS,
        obs_switch_delay_after_blackout_seconds: float = DEFAULT_OBS_SWITCH_DELAY_AFTER_BLACKOUT_SECONDS,
        obs_switch_timeout_seconds: float = DEFAULT_OBS_SWITCH_TIMEOUT_SECONDS,
        rank_stability_monitor: Optional[StabilityMonitor] = None,
        rank_ocr_executor: Optional["concurrent.futures.Executor"] = None,
        goal_ocr_executor: Optional["concurrent.futures.Executor"] = None,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now_fn = now_fn
        self._league_change_grace_seconds = league_change_grace_seconds
        self._rank_recheck_interval_seconds = rank_recheck_interval_seconds
        self._vs_screen_lockout_seconds = vs_screen_lockout_seconds
        self._obs_switch_delay_after_blackout_seconds = obs_switch_delay_after_blackout_seconds
        self._obs_switch_timeout_seconds = obs_switch_timeout_seconds
        # Issue #388: 「同じ値が持続しているか」を確認するデバウンスは共通の
        # _Debounceヘルパーに委ねる(モジュールdocstring参照)
        self._banner_debounce = _Debounce(banner_confirm_seconds)
        self._banner_debounce_after_match_end = _Debounce(banner_confirm_seconds_after_match_end)
        self._banner_absence_debounce = _Debounce(banner_absence_confirm_seconds)
        self._goal_debounce = _Debounce(goal_confirm_seconds)
        self._vs_screen_debounce = _Debounce(vs_screen_confirm_seconds)
        self._match_end_debounce = _Debounce(match_end_confirm_seconds)
        self._demotion_label_debounce = _Debounce(demotion_label_confirm_seconds)
        self._pending_gauge_debounce = _Debounce(rank_recheck_interval_seconds)
        self._rank_monitor = rank_stability_monitor or StabilityMonitor(roi=rank_roi)
        # Issue #397: VS画面ランクOCR(_run_vs_screen_ocr)と結果バナー確定時の
        # ランクバッジ読み取り(_run_rank_before_ocr)用。この2つは同じ試合の中で
        # 「試合開始時」「試合終了時」に分かれて走り決して同時には走らないため、
        # ワーカープロセスを1つ共有する(#327のgoal_ocr_executorと分けるのは、
        # ゴール検知は試合中ずっと走りうるため)
        self._rank_ocr_executor: concurrent.futures.Executor = (
            rank_ocr_executor
            if rank_ocr_executor is not None
            else concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rank-ocr")
        )
        # Issue #327: ゴール検知のOCR一式(_run_goal_ocr)用。未指定時は
        # ThreadPoolExecutorを使う(同一プロセス内で動くためテストの
        # モンキーパッチがそのまま効く)。本番はmain.pyがProcessPoolExecutorを
        # 明示的に渡す(モジュールdocstring参照)
        self._goal_ocr_executor: concurrent.futures.Executor = (
            goal_ocr_executor
            if goal_ocr_executor is not None
            else concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="goal-ocr")
        )

        self._state = _State.WATCHING
        self._rank_phase = _RankPhase.WAITING_STABLE
        # Issue #388: GRACE満了待ちの起点(None=GRACE未開始)。
        # now - self._grace_started_at >= self._league_change_grace_seconds で判定
        self._grace_started_at: Optional[float] = None
        self._banner_candidate: BannerResult = None
        self._pending_result: BannerResult = None
        # 帯番号(int)はleague_changed判定に、小数のランク値(float)はMatchResultの
        # 報告値に使う。ゲージの溜まり具合による僅かな変動をリーグ変動と
        # 誤検知しないよう、判定には必ず帯番号(整数)側を使うこと
        self._pending_rank_before_tier: Optional[int] = None
        self._pending_rank_before: Optional[float] = None
        self._grace_candidate_rank_tier: Optional[int] = None
        # Issue #178: ゲージの塗りつぶし(小数部)の確定値。確定時にはスナップショット
        # ではなくこの値を使う。Issue #235: 生値をそのまま信頼せず、下記
        # _pending_gauge_fill/_pending_gauge_debounceによるデバウンスを経て
        # 直近rank_recheck_interval_seconds秒分連続で一致して初めて更新される
        self._latest_gauge_fill: Optional[float] = None
        # Issue #235: 確定候補中のゲージ値。遷移演出(ワイプ演出による急騰・
        # バッジ消失によるフェード後の急落)由来の一時的なノイズが
        # _latest_gauge_fillへ混入するのを防ぐためのデバウンス用
        # (何秒連続で一致しているかは_pending_gauge_debounceが持つ)
        self._pending_gauge_fill: Optional[float] = None
        # Issue #384: ランクゲージDEBUGログを値変化時のみ出力するための、
        # 直近ログ出力時の生値(_pending_gauge_fillとは別に保持する)
        self._last_logged_gauge_fill: Optional[float] = None
        # Issue #423: 「試合終了」確認後のBANNER_ROIS実測値ログ(_log_banner_stats参照)を
        # 間引くため、前回ログに出した値を保持する。60fpsのまま毎フレーム出すと
        # DEBUGログ全体のノイズになるため、Issue #384のゲージログと同じ考え方にした
        self._last_logged_banner_stats: Optional[tuple[float, float, float]] = None
        # Issue #327: ゴール検知のOCR一式(_run_goal_ocr)を_goal_ocr_executorで
        # 非同期実行するための状態。_goal_ocr_futureが非Noneの間は多重に投げない
        self._goal_ocr_future: Optional["concurrent.futures.Future"] = None
        # Issue #430: 結果バナー確定時の試合前ランク読み取り(_run_rank_before_ocr)を
        # 待たずにTRACKING_RANKへ進むための状態。非Noneの間は結果待ち
        # (_poll_rank_before_ocr参照)
        self._rank_before_future: Optional["concurrent.futures.Future"] = None
        self._rank_before_match_no: int = 0
        # Issue #136: 昇格演出(is_league_change_screen)がこの試合中に一度でも
        # 観測されたか。帯番号の急変を検証する際、昇格側はこの独立信号で
        # 確認できていない限り認めない
        self._promotion_confirmed_this_match = False
        # Issue #176: 降格ラベル(is_demotion_label_candidate/confirm_demotion_label_text)を
        # この試合中に確認できたか。_infer_tier_after()でゲージ小数部の
        # 間接推測より優先して使う独立信号
        self._demotion_confirmed_this_match = False
        self._demotion_label_recorded_this_event = False
        self._goal_recorded_this_event = False
        self._pending_goals: list[GoalEvent] = []
        self._vs_recorded_this_match = False
        # Issue #234/#388: VS画面確定直後からロックする締切時刻。Noneまたは
        # now以前の間は通常通りVS画面検知を行う(_check_for_vs_screen参照)
        self._vs_lockout_until: Optional[float] = None
        # Issue #229: _vs_recorded_this_matchはVS画面が視覚的に消えた(is_vs_screenが
        # Falseに戻った)瞬間にFalseへ戻ってしまう(_check_for_vs_screen参照。VS画面
        # OCRの重複発火を防ぐための「今まさにVS画面が出ていて記録済みか」という
        # 短命なフラグのため)。「この試合でVS画面を一度でも確認できたか」を試合の
        # 終わりまで覚えておく別フラグとして用意する(_finalize()でのみFalseに戻す)
        self._vs_confirmed_this_match = False
        self._pending_vs_mine_ranks: list[SlotRank] = []
        self._pending_vs_opponent_ranks: list[SlotRank] = []
        self._pending_mine_team_color: Optional[str] = None
        self._pending_opponent_team_color: Optional[str] = None
        # Issue #145: VS画面確定を検知した直後の1フレームだけpop_vs_screen_event()が
        # 返す値。取得されると(popされると)Noneに戻る「取得したら消費される」設計
        self._vs_screen_event: Optional[VsScreenEvent] = None
        # Issue #397: VS画面OCR(_run_vs_screen_ocr)を_rank_ocr_executorで
        # 非同期実行するための状態。_vs_ocr_futureが非Noneの間は結果待ち
        # (_poll_vs_ocr参照)。Issue #189の頃はバックグラウンドスレッドが
        # _pending_vs_*系フィールドを直接書き込んでいたためロックが必要だったが、
        # 結果の取り込みをメインスレッド(_poll_vs_ocr)に集約したため不要になった
        self._vs_ocr_future: Optional["concurrent.futures.Future"] = None
        self._vs_ocr_match_no: int = 0
        self._match_end_recorded_this_event = False
        self._match_end_seen = False
        # Issue #190: _match_end_seenはbanner確定時のデバウンス短縮用にすぐ
        # リセットされてしまうため、_finalize()到達時点まで確認結果を持ち越す
        # 別フラグ。OBSシーン切替(in_match)の必須条件にのみ使う
        # (MatchResultの記録自体は従来どおり、このフラグの有無に関わらず行う)
        self._match_end_confirmed_this_match = False
        # Issue #83: OBSシーン切替のトリガー用。VS画面確定でTrue、暗転検知から
        # obs_switch_delay_after_blackout_seconds経過後にFalseに戻す(Issue #224)
        self._in_match = False
        # Issue #224: 「試合終了」を確認済み(=in_matchをFalseに戻す予定がある)だが、
        # まだ暗転を検知できていない間True。_check_pending_obs_switch()がこのフラグを
        # 見て毎フレーム暗転をチェックする。
        # Issue #371: Trueにする場所は_finalize()から_check_for_match_end()へ移した
        # (_finalize()はランク確定の長さに引きずられ、暗転1に間に合わないため)
        self._pending_obs_switch = False
        # 暗転を最初に検知した時刻。Noneはまだ暗転未検知(Issue #388)
        self._blackout_switch_started_at: Optional[float] = None
        # Issue #383: 暗転の取りこぼし原因究明用。_pending_obs_switchがTrueに
        # なった時刻(time.monotonic())と、その間に観測した最小輝度平均。
        # 「暗転自体は早期に検知できているのに切替が遅い」のか「暗転そのものを
        # 長時間観測できていない」のかをDEBUGログから切り分けるために使う
        # (_check_pending_obs_switch()参照)
        self._pending_obs_switch_started_at: Optional[float] = None
        self._pending_obs_switch_min_mean: Optional[float] = None
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

    @property
    def session_match_no(self) -> int:
        """この配信セッションで何試合目か(ログ表記と同じ番号、Issue #423)。

        ログ・保存ファイル名を突き合わせられるようにするための観測用。
        """
        return self._session_match_no

    @property
    def match_end_seen(self) -> bool:
        """「試合終了」をOCR確認済みで、まだ結果バナーが確定していない間True(Issue #423)。

        専用部屋の負けバナーが検知できず試合が丸ごと記録されない不具合の調査用。
        main.pyがこの区間のフレームを静止画として保存する(banner_debug_frames参照)。
        結果バナーの確定・誤検知としての破棄のどちらでもFalseに戻る。
        """
        return self._match_end_seen

    @property
    def current_match_has_rank(self) -> bool:
        """現在の試合がランクを賭けた試合か(VS画面で自分のランクバッジを読めたか、Issue #430)。

        結果バナー確定時にTRACKING_RANKへ進むかどうか(Issue #235)と同じ判定。
        main.pyが「試合終了」確認の時点でランク手動入力用クリップの録画を
        始めるかどうかを決めるのに使う(結果バナー確定より前に分かる必要があるため)。
        """
        return bool(self._pending_vs_mine_ranks and self._pending_vs_mine_ranks[0].tier is not None)

    def pop_vs_screen_event(self) -> Optional[VsScreenEvent]:
        """VS画面を確定した直後の1フレームだけVsScreenEventを返す(Issue #145)。

        process_frame()と同じ「取得したら消費される」設計。呼び出すと内部の
        保持値はNoneに戻るため、main.py側はprocess_frame()を呼ぶたびに毎回
        これも呼び、Noneでなければその場でDBへ即時反映すること。
        """
        event = self._vs_screen_event
        self._vs_screen_event = None
        return event

    def process_frame(
        self, frame: np.ndarray, blackout: Optional[BlackoutObservation] = None
    ) -> Optional[MatchResult]:
        """フレームを1枚処理する。

        Issue #398: blackoutには、前回のprocess_frame()以降にキャプチャ側が
        受け取った全フレームの暗転観測結果(detection.motion.BlackoutWatcher)を
        渡せる。渡された場合、暗転の判定にはこのフレーム単体ではなくその観測結果を
        使う(検知ループが重い処理で止まっている間に過ぎ去った0.40秒の暗転を
        取りこぼさないため、モジュールdocstring参照)。省略した場合は従来どおり
        このフレーム単体をis_full_blackout()で判定する。
        """
        # Issue #388: このフレームの処理全体を通して同じ時刻を使う
        # (デバウンス判定の途中で時刻がずれないよう、1回だけ取得する)
        now = self._now_fn()
        # Issue #430: 試合前ランクの読み取り結果が届いていれば、状態の振り分けより
        # 前に取り込む(_track_rank()が帯番号の起点として使うため)
        self._poll_rank_before_ocr()
        if self._state is _State.WATCHING:
            self._check_for_vs_screen(frame, now)
            self._check_for_goal(frame, now)
            self._check_for_match_end(frame, now)
            result = self._watch_for_banner(frame, now)
        elif self._state is _State.TRACKING_RANK:
            result = self._track_rank(frame, now, blackout)
        else:
            result = self._watch_for_banner_absence(frame, now)
        # Issue #224: 状態振り分けの「後」で呼ぶこと。_finalize()がis_full_blackout()
        # 自体をトリガーに呼ばれるケース(Issue #209の暗転即時確定パス)では、
        # _pending_obs_switchがTrueになるのはこのprocess_frame()呼び出しの
        # 途中(_track_rank内)のため、先頭で呼ぶとまだFalseのまま素通りしてしまい、
        # 同じフレームが暗転そのものであることに気づけない(モジュールdocstring参照)
        self._check_pending_obs_switch(frame, now, blackout)
        # Issue #327: ゴールOCRの結果が届いた時点で状態がWATCHINGから進んでいても
        # (_pending_goalsへの追加は_finalize()まで有効なため)取りこぼさないよう、
        # 状態に関わらず毎フレーム呼ぶ(_check_pending_obs_switchと同じ考え方)
        self._poll_goal_ocr()
        # Issue #397: VS画面OCRの結果も、状態がWATCHINGから進んでいても
        # 取りこぼさないよう状態に関わらず毎フレーム取り込む
        self._poll_vs_ocr()
        return result

    def _check_pending_obs_switch(
        self, frame: np.ndarray, now: float, blackout: Optional[BlackoutObservation] = None
    ) -> None:
        """Issue #224: 「試合終了」確認済みで暗転待ちの間、毎フレーム暗転を監視する。

        暗転を最初に検知した瞬間からobs_switch_delay_after_blackout_seconds分の
        単純なタイマーを開始し(暗転自体の継続時間は0.3〜0.5秒程度とこの既定値
        より短いため、その後暗転が終わってもタイマーはリセットしない)、
        経過したらin_matchをFalseに戻す(モジュールdocstring参照)。

        Issue #371: 待ち受けの開始点が_check_for_match_end()(「試合終了」OCR確認)へ
        前倒しされたため、ここで最初に捕まえる暗転は暗転1(実測で試合終了の
        7〜9秒後)になる。ランク確定(_finalize())がどれだけ長引いても
        取りこぼさない一方、暗転1から実際の切替までの間隔は
        obs_switch_delay_after_blackout_seconds(既定5秒相当)がそのまま決める。

        Issue #383: 暗転自体を取りこぼす(=このメソッドに暗転を満たすフレームが
        一度も渡ってこない)事象が実配信で見つかったが、既存のログでは
        「暗転をいつ検知したか」「検知できなかった間どれだけ暗いフレームを
        観測できていたか」が一切分からず原因究明ができなかった。そこで
        暗転をまだ検知していない間、フレームごとの輝度をこのメソッド内で
        直接見て以下の2種類のDEBUGログを出す(is_full_blackout()を素通しで
        呼ぶだけだと閾値の内訳が分からないため、frame_brightness_stats()で
        平均・標準偏差を自前で見て閾値判定する。呼んでいる関数・使っている
        閾値定数自体はis_full_blackout()と同一で、判定結果は変えていない):

        Issue #387: 上記の原因究明ログ追加(frame_brightness_stats()呼び出し)が、
        直後のis_full_blackout(frame)内部でも同じ計算をもう一度行わせてしまい、
        1920x1080全画面のcv2.cvtColor+mean/std(実測8ms前後)を1フレームにつき
        実質2回実行する形になっていた。この暗転待ち区間だけ検知ループの実効
        レートが60fps→約28fpsまで半減し、0.55秒しかない暗転自体を取りこぼす
        (本Issue)のに加え、表示時間の短い「勝ち」結果バナーの確定も落としていた
        (#387)。対応はframe_brightness_stats()自体を間引きサンプリングに変更する
        形にした(detection/motion.pyのモジュールdocstring参照、間引き後は2回
        呼んでも合計2ms程度)。is_full_blackout()はテストからモジュール直下の
        名前でmonkeypatchされ暗転タイミングを厳密制御する使われ方をしているため、
        判定経路をこの関数の戻り値以外に置き換えることはしていない(呼び出し
        構造・閾値定数・判定結果は変えていない)。

        - 暗転待ち区間で観測した最小輝度平均を更新するたびに1行(「一番暗い
          フレームでもどこまでしか暗くならなかったか」を残す。区間全体で
          最も暗かったフレームの記録なので、更新のたびに出しても頻度は
          自然に低い)
        - 暗転(候補)を検知した瞬間(_blackout_switch_started_atがNoneから
          セットされる瞬間)に1行(「試合終了」からの経過秒数込み)。これがあれば、次に
          同様の遅延が起きた際「暗転自体は早期に検知できているのに切替までの
          5秒待ちの方に問題があるのか」「暗転そのものを長時間観測できて
          いないのか」をログだけで切り分けられる
        """
        if not self._pending_obs_switch:
            return
        if self._blackout_switch_started_at is None:
            # Issue #395: 暗転を取りこぼした場合の安全網。「試合終了」OCR確認から
            # obs_switch_timeout_secondsを過ぎても暗転を一度も検知できていなければ、
            # 暗転を待たずに切り替える(モジュールdocstring参照)。この経路を通った
            # ことは必ずWARNINGで残す: 安全網は症状を隠す対策のため、隠したことが
            # 見えないと根本対策(#397/#398)の効果を後から測れなくなる
            elapsed = now - self._pending_obs_switch_started_at
            if elapsed >= self._obs_switch_timeout_seconds:
                observed_min = (
                    "未観測"
                    if self._pending_obs_switch_min_mean is None
                    else f"{self._pending_obs_switch_min_mean:.1f}"
                )
                logger.warning(
                    "%d試合目: 暗転を検知できないまま%.1f秒が経過したため、"
                    "タイムアウトでOBSシーンを切り替えます(この間に観測した最小輝度mean=%s)",
                    self._session_match_no,
                    elapsed,
                    observed_min,
                )
                self._complete_obs_switch()
                return
            # Issue #383: 判定そのものは既存どおりis_full_blackout()(テストで
            # monkeypatch対象になっているモジュール直下の名前)に委ね、
            # frame_brightness_stats()はログ表示用の値取得にのみ使う
            # (判定結果は変えない)。Issue #387: 両方とも間引き済みの
            # frame_brightness_stats()を経由するため、2回呼んでも合計2ms程度
            # (メソッドdocstring参照)
            # Issue #398: キャプチャ側の観測結果があればそちらを使う
            # (メインループが止まっていた間のフレームも含まれる)
            if blackout is not None:
                mean = blackout.min_mean
                std = blackout.min_std
                if mean is None:
                    # この区間に1枚もフレームが届いていない
                    return
            else:
                mean, std = frame_brightness_stats(frame)
            if self._pending_obs_switch_min_mean is None or mean < self._pending_obs_switch_min_mean:
                self._pending_obs_switch_min_mean = mean
                logger.debug(
                    "暗転待ち中の最小輝度を更新しました: mean=%.1f std=%.1f (試合終了から%.1f秒)",
                    mean,
                    std,
                    now - self._pending_obs_switch_started_at,
                )
            if not (blackout.blackout if blackout is not None else is_full_blackout(frame)):
                return
            logger.debug(
                "暗転(候補)を検知しました: mean=%.1f std=%.1f (試合終了から%.1f秒)",
                mean,
                std,
                now - self._pending_obs_switch_started_at,
            )
            self._blackout_switch_started_at = now
        if (now - self._blackout_switch_started_at) >= self._obs_switch_delay_after_blackout_seconds:
            self._complete_obs_switch()

    def _complete_obs_switch(self) -> None:
        """in_matchをFalseへ戻し、暗転待ちの状態を片付ける(Issue #395で共通化)。

        通常の経路(暗転検知+obs_switch_delay_after_blackout_seconds経過)と、
        暗転を取りこぼした場合のタイムアウト経路の両方から呼ばれる。
        """
        self._in_match = False
        self._pending_obs_switch = False
        self._blackout_switch_started_at = None
        self._pending_obs_switch_started_at = None
        self._pending_obs_switch_min_mean = None

    def _check_for_vs_screen(self, frame: np.ndarray, now: float) -> None:
        # Issue #234: VS画面確定直後はロック中(この秒数は新規のVS画面検知自体を
        # 行わない)。演出中の一瞬の判定揺れでis_vs_screenが1フレームだけFalseに
        # なると_vs_recorded_this_matchが即座にリセットされてしまい(下記参照)、
        # その後streakが再度積み上がると同じVS画面のまま試合開始が二重に発火する
        # 不具合の対症療法。ロック中は判定を丸ごとスキップする(Issue #388: カウント
        # ダウン式からnow基準の締切時刻式に変更)
        if self._vs_lockout_until is not None and now < self._vs_lockout_until:
            return
        self._vs_lockout_until = None
        if not is_vs_screen(frame):
            self._vs_screen_debounce.reset()
            self._vs_recorded_this_match = False
            return

        if self._vs_screen_debounce.observe(True, now) and not self._vs_recorded_this_match:
            # Issue #243: 前の試合が結果画面確定(_finalize())前にVS画面が
            # 再確定した場合、前の試合の_pending_goals等は_finalize()での
            # クリアを経ないままこの新しい試合に持ち越されてしまう(検証で
            # 実際に発生を確認、詳細はモジュールdocstring参照)。挙動は変えず、
            # 気づけるようにログだけ出す。通信切断等によるゲーム強制終了でも
            # 起こりうる正常な状態遷移のため、必ずしも不具合とは限らない
            if self._vs_confirmed_this_match:
                # Issue #423: 「試合終了」をOCR確認できていた場合は、通信切断等の
                # 正常な状態遷移ではなく「結果バナーを検知できずに試合を丸ごと
                # 取りこぼした」ことがほぼ確定するため、INFOではなくWARNINGにする。
                # 2026-09-08の専用部屋配信では18試合中8試合(すべて負け)がこの経路で
                # 何の警告も出ないまま消えており、配信録画と突き合わせるまで
                # 気づけなかった(モジュールdocstring参照)
                if self._match_end_seen:
                    logger.warning(
                        "%d試合目: 「試合終了」を確認済みなのに結果バナーを確定できないまま"
                        "次のVS画面を検知しました。この試合は記録されません"
                        "(バナーの色が閾値を外している可能性。直前の「試合終了後のバナーROI実測」の"
                        "DEBUGログとclips/banner_debug_frames/の静止画を確認してください)",
                        self._session_match_no,
                    )
                logger.info(
                    "%d試合目: 前の試合が結果画面確定前に次のVS画面を検知しました。"
                    "前の試合のゴール(%d件)は今回の試合の記録に持ち越されます"
                    "(通信切断等によるゲーム強制終了でも起こりうるため、必ずしも不具合とは限りません)",
                    self._session_match_no,
                    len(self._pending_goals),
                )
            self._vs_recorded_this_match = True
            self._vs_confirmed_this_match = True
            self._in_match = True
            # Issue #234/#388: 確定した瞬間からロックを開始する(締切時刻式)
            self._vs_lockout_until = now + self._vs_screen_lockout_seconds
            # Issue #224: 前の試合の暗転待ち(_pending_obs_switch)が何らかの理由で
            # 完了しないまま次の試合のVS画面が先に確定した場合(実際にはほぼ
            # 起きないはずのレアケース)、in_match=Trueが優先されるべきなので
            # 古い切替待ちは破棄する
            if self._pending_obs_switch and self._pending_obs_switch_started_at is not None:
                # Issue #383: 暗転待ちが次の試合開始まで一度も完了しなかった
                # (=暗転を検知できないまま破棄された)ケースを追えるようにする
                logger.warning(
                    "前の試合の暗転待ちが完了しないまま次の試合が始まったため破棄します"
                    "(待機時間: 約%.1f秒、観測できた最小輝度平均: %s)",
                    now - self._pending_obs_switch_started_at,
                    f"{self._pending_obs_switch_min_mean:.1f}" if self._pending_obs_switch_min_mean is not None else "観測なし",
                )
            self._pending_obs_switch = False
            self._blackout_switch_started_at = None
            self._pending_obs_switch_started_at = None
            self._pending_obs_switch_min_mean = None
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
            # Issue #397: スレッドではPaddleOCR推論中にGILが解放されず
            # メインループも道連れで止まる(実測4.4〜4.8秒)ため、
            # _rank_ocr_executor(本番はProcessPoolExecutor)へ投げる
            self._vs_ocr_match_no = self._session_match_no
            self._vs_ocr_future = self._rank_ocr_executor.submit(_run_vs_screen_ocr, frame)

    def _poll_vs_ocr(self, wait: bool = False) -> None:
        """_check_for_vs_screen()が投げたVS画面OCR(Issue #397)の結果を取り込む。

        通常(wait=False)はブロックせず、まだ実行中の間は何もしない。
        wait=Trueの場合(_finalize()からの呼び出し)は完了を待ってから取り込む
        (未完了のままMatchResultを組むとVS画面のランクが空のまま記録される
        ため、Issue #189/#397の_poll_vs_ocr(wait=True)と同じ理由)。
        """
        if self._vs_ocr_future is None:
            return
        if not wait and not self._vs_ocr_future.done():
            return
        result = self._vs_ocr_future.result()
        self._vs_ocr_future = None
        match_no = self._vs_ocr_match_no

        self._pending_vs_mine_ranks = result.mine_ranks
        self._pending_vs_opponent_ranks = result.opponent_ranks
        self._pending_mine_team_color = result.mine_team_color
        self._pending_opponent_team_color = result.opponent_team_color
        # Issue #145: 試合結果確定(MatchResult)を待たず、main.py側が
        # 次にprocess_frame()を呼んだタイミングですぐDBへ反映できるようにする
        # (pop_vs_screen_event参照)
        self._vs_screen_event = VsScreenEvent(
            mine_ranks=result.mine_ranks,
            opponent_ranks=result.opponent_ranks,
            mine_team_color=result.mine_team_color,
            opponent_team_color=result.opponent_team_color,
            session_match_no=match_no,
        )
        # Issue #121: ゴール検知(_check_for_goal)と同じく、DBへの記録タイミング
        # (main.py側のsave_vs_slot_ranks時)を待たず、OCRが完了した時点で
        # 読み取ったランクをそのまま報告する
        logger.info(
            "%d試合目 VS画面ランク: mine=%s opponent=%s",
            match_no,
            result.mine_ranks,
            result.opponent_ranks,
        )
        logger.info(
            "%d試合目 チームカラー: mine=%s opponent=%s",
            match_no,
            result.mine_team_color,
            result.opponent_team_color,
        )

    def _check_for_match_end(self, frame: np.ndarray, now: float) -> None:
        if not is_match_end_screen(frame):
            self._match_end_debounce.reset()
            self._match_end_recorded_this_event = False
            return

        if self._match_end_debounce.observe(True, now) and not self._match_end_recorded_this_event:
            self._match_end_recorded_this_event = True
            # is_match_end_screenは色ベースの候補判定のため、「延長戦」「キックオフ」等の
            # 誤検知をここでOCRにより除外する(detection.match_end参照)
            if confirm_match_end_text(frame):
                self._match_end_seen = True
                self._match_end_confirmed_this_match = True
                # Issue #371: 暗転待ちの開始点はここ(「試合終了」OCR確認)であって
                # _finalize()ではない。_finalize()はランク確定(GRACE)の長さに
                # 引きずられて暗転1より後になることがあり、その場合に切替が
                # 暗転2以降まで持ち越されてしまうため(モジュールdocstring参照)
                self._pending_obs_switch = True
                self._pending_obs_switch_started_at = now
                self._pending_obs_switch_min_mean = None
                logger.info("%d試合目 試合終了", self._session_match_no)

    def _check_for_goal(self, frame: np.ndarray, now: float) -> None:
        if not is_goal_event(frame):
            self._goal_debounce.reset()
            self._goal_recorded_this_event = False
            return

        if (
            self._goal_debounce.observe(True, now)
            and not self._goal_recorded_this_event
            and self._goal_ocr_future is None
        ):
            self._goal_recorded_this_event = True
            # Issue #383: 暗転の取りこぼしがCPU競合(PaddleOCR実行中の負荷)と
            # 時間的に重なっていないかを事後に突き合わせられるよう、投入時刻を
            # 出す(完了時刻は_poll_goal_ocr()の「ゴール検知」ログで既に出ている)
            logger.debug("ゴールOCR一式を投入しました(別プロセス、goal-ocr)")
            self._goal_ocr_future = self._goal_ocr_executor.submit(_run_goal_ocr, frame)

    def _poll_goal_ocr(self, wait: bool = False) -> None:
        """_check_for_goal()が投げたOCR一式(Issue #327)の結果を取り込む。

        通常(wait=False)はブロックせず、まだ実行中の間は何もしない。結果が届く
        タイミングは呼び出し元では制御しない(状態がWATCHINGから進んでいても、
        _pending_goalsへの追加はfinalize()まで有効なためそのまま適用する)。

        wait=Trueの場合(_finalize()からの呼び出し、_poll_vs_ocr(wait=True)と同じ理由)は
        完了を待ってから取り込む。ゴールの帯番号再チェック(Issue #303の
        帯番号の定期再チェック(Issue #396で廃止)と異なり、ゴール自体は取りこぼすとその1件が
        MatchResult.goalsに載らないまま永久に失われてしまう(次の試合の
        _pending_goalsに紛れ込ませるのはさらに悪い、誤った試合に記録されてしまう)
        ため、読み捨てを許容せず待つ設計にしている。
        """
        if self._goal_ocr_future is None:
            return
        if not wait and not self._goal_ocr_future.done():
            return
        result = self._goal_ocr_future.result()
        self._goal_ocr_future = None

        # is_goal_eventは色ベースの候補判定のため、青空・スタジアム天蓋の映り込み
        # (Issue #186)等の誤検知をここでOCRにより除外する(detection.goal参照)
        if not result.confirmed:
            logger.info(
                "ゴール候補を検知しましたが、得点者名パネルのラベルを確認できなかったため誤検知として無視します"
            )
            return

        scorer = result.scorer
        assist = result.assist
        # Issue #71: OCRの誤読診断のため、信頼度スコア込みの実名をDEBUGレベルに
        # 限り出す(CLAUDE.md「ログ方針」の例外)
        logger.debug("ゴール検知: scorer=%s assist=%s", scorer, assist)

        scorer_name = scorer[0] if scorer is not None else None
        assist_name = assist[0] if assist is not None else None
        # Issue #217: オウンゴールは得点者名パネル自体が表示されないため
        # scorer_name/assist_nameは常にNoneのまま(detection.goalのdocstring参照)。
        # 判定結果は見えたものをそのまま報告するのみで、GOAL_RECORD_MODEに応じた
        # 記録可否の判断は永続化層(database.db.save_goal)の責務のまま変更しない
        is_own_goal = result.is_own_goal
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
        elif mode == "allowlist_redact" and (not scorer_allowed or (assist_name is not None and not assist_allowed)):
            status = "一部redactして記録対象"
        else:
            status = "記録対象"
        logger.info(
            "%d試合目 ゴール検知: scorer=%s assist=%s (%s)",
            self._session_match_no,
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

    def _poll_rank_before_ocr(self, wait: bool = False) -> None:
        """_watch_for_banner()が投げた試合前ランク読み取り(Issue #430)の結果を取り込む。

        通常(wait=False)はブロックせず、まだ実行中の間は何もしない。wait=Trueは
        結果が無いと先へ進めない場面(ランクを賭けない試合の即時確定・暗転での確定・
        _finalize())で使う。_poll_vs_ocr()/_poll_goal_ocr()と同じ設計。

        取り込んだ時点で試合前ランクを確定させ、「n試合目の結果」ログを出す
        (以前は結果バナー確定と同じフレームで出していたが、Issue #430で読み取りを
        待たなくなったため、値が揃うこの時点へ移した)。TRACKING_RANK中の帯番号の
        起点(_grace_candidate_rank_tier)もここで埋める。
        """
        if self._rank_before_future is None:
            return
        if not wait and not self._rank_before_future.done():
            return
        ocr = self._rank_before_future.result()
        self._rank_before_future = None
        match_no = self._rank_before_match_no

        precise_result = self._apply_rank_before_ocr(ocr)
        if precise_result is not None:
            self._pending_rank_before_tier, self._pending_rank_before = precise_result
        else:
            self._pending_rank_before_tier = None
            self._pending_rank_before = None
            logger.info(
                "%d試合目: 結果バナー確定時点でランクバッジを読み取れませんでした"
                "(バッジ非表示、または読み取り失敗の可能性)",
                match_no,
            )
        logger.info(
            "%d試合目の結果: %s (ランク(試合前): %s)",
            match_no,
            _BANNER_RESULT_LABELS[self._pending_result],
            self._pending_rank_before if self._pending_rank_before is not None else "なし",
        )
        # Issue #396: GRACE中は帯番号OCRを行わないため、帯番号の起点は
        # 結果バナー確定時に読み取った試合前の帯番号にする(モジュール
        # docstring参照)。昇格/降格による±1は_infer_tier_after()が確定時に適用する
        self._grace_candidate_rank_tier = self._pending_rank_before_tier

    def _apply_rank_before_ocr(self, ocr: RankBeforeOcrResult) -> Optional[tuple[int, float]]:
        """結果バナー確定時点のランクバッジ読み取り結果から試合前ランクを決める(Issue #222)。

        バナー確定直後は「まだコンパクト表示のはず」という前提が大半のケースで
        成り立つが、「試合終了」バナー消灯からバナー色判定の確定までにかかる
        時間が長引くと、確定した頃には既にバッジがコンパクト→拡大のアニメーション
        を終えている(またはアニメーション中の)ことが実データで確認されている
        (モジュールdocstring・Issue #222参照)。どちらのサイズになっているかを
        事前に判定する手段が無いため、両方のROIで`read_precise_rank`を試し、
        読み取れた方を採用する。

        間違ったROI(コンパクト表示にENLARGED、拡大表示にCOMPACT)を当てた場合は
        常にNoneを返すことをfixtures/screenshots 4件の実データで確認済み
        (Issue #222の調査コメント参照)のため、通常はどちらか一方だけが
        成功する。遷移アニメーション中の中間状態のフレームでごくまれに両方
        成功してしまった場合は、値が食い違うかどうかに関わらずコンパクト側を
        優先する(バナー確定直後は本来コンパクト表示のはず、という設計上の
        期待に合わせるため)。食い違い自体はWARNINGログに残し、実際に発生する
        頻度・どちらの値が正しいことが多いかは今後のデータで判断する。

        Issue #283: 帯番号(整数部)は、ここでのOCRよりVS画面読み取り
        (vs_rank.py、PaddleOCRベース)の方が実測で大幅に精度が高いことが
        実機ログで判明した。ゲージの溜まり具合(小数部)はここでのHSV読み取りの
        まま使うが、帯番号は`self._pending_vs_mine_ranks[0]`(VS画面確定時に
        読み取った自分のランク)が'∞'帯かつ数値を取得できていれば、ここでの
        OCR結果を捨ててそちらを常に優先する(食い違っていてもVS画面側を採用、
        ユーザーと合意済み)。S/A帯・VS画面を確認できなかった試合は対象外
        (rank_before/afterの追跡自体が∞帯のみを前提にしているため)で、
        従来通りここでのOCR結果にフォールバックする。ここでのOCRがcompact/
        enlargedどちらのROIでもバッジ自体を読み取れなかった場合(バッジ非表示、
        またはゲージも含め完全な読み取り失敗)は、VS画面側の情報があっても
        値を捏造せずNoneのまま返す(「バッジが無い」ことと「帯番号だけ誤読した」
        ことは別の状況のため)。Issue #136の帯変化の妥当性チェック・再スキャン・
        ゲージ連続性フォールバックは、VS画面側も100%ではないため二段構えの
        保険としてそのまま残す。
        """
        # Issue #397/#430: OCR自体は_watch_for_banner()が_rank_ocr_executorへ投げ、
        # 結果は_poll_rank_before_ocr()が受け取ってここへ渡す
        compact_result = ocr.compact
        enlarged_result = ocr.enlarged
        if compact_result is not None and enlarged_result is not None and compact_result != enlarged_result:
            logger.warning(
                "結果バナー確定時点でコンパクト/拡大どちらのROIでもランクバッジが読み取れ、"
                "値が食い違っています(compact=%s enlarged=%s)。コンパクト側を採用します",
                compact_result,
                enlarged_result,
            )
        ocr_result = compact_result if compact_result is not None else enlarged_result
        if ocr_result is None:
            return None

        vs_mine_rank = self._pending_vs_mine_ranks[0] if self._pending_vs_mine_ranks else None
        vs_tier = vs_mine_rank.value if vs_mine_rank is not None and vs_mine_rank.tier == "∞" else None
        if vs_tier is None or vs_tier == ocr_result[0]:
            return ocr_result

        logger.warning(
            "結果バナー確定時点の帯番号OCR(%s)がVS画面の読み取り(%s)と食い違っています。"
            "VS画面側を採用します",
            ocr_result[0],
            vs_tier,
        )
        fill = ocr_result[1] - ocr_result[0]
        return vs_tier, vs_tier + fill

    def _watch_for_banner(self, frame: np.ndarray, now: float) -> Optional[MatchResult]:
        result = classify_banner(frame)
        if self._match_end_seen:
            self._log_banner_stats(frame, result)
        if result != self._banner_candidate:
            self._banner_debounce.reset()
            self._banner_debounce_after_match_end.reset()
            self._banner_candidate = result
        # Issue #388: 両方のデバウンスに同じ観測を流し、確定判定にどちらの
        # 閾値を使うかだけを_match_end_seenで切り替える(同じストリークの
        # 途中でmatch_end_seenがFalse→Trueに変わった場合でも、既に積んだ
        # 経過秒数を引き継いだまま短いデバウンス側で即座に確定できる、
        # Issue #76が意図した挙動を保つため)
        confirmed_after_match_end = self._banner_debounce_after_match_end.observe(result is not None, now)
        confirmed_default = self._banner_debounce.observe(result is not None, now)
        confirmed = confirmed_after_match_end if self._match_end_seen else confirmed_default

        if confirmed:
            # Issue #229: 試合の区切りをVS画面確定に一本化する。この試合でVS画面を
            # 一度も確認できていない状態で結果バナーが確定した場合、直前の試合の
            # 残像(暗転〜マッチング画面手前のどこかの画面)を誤って結果バナーとして
            # 拾った可能性が高いとみなし、新しい試合としては記録しない
            # (モジュールdocstring参照)
            if not self._vs_confirmed_this_match:
                logger.info(
                    "%d試合目: VS画面を確認できないまま結果バナー(%s)を検知しました。"
                    "直前の試合の誤検知とみなし記録をスキップします",
                    self._session_match_no,
                    _BANNER_RESULT_LABELS[self._banner_candidate],
                )
                self._banner_candidate = None
                self._banner_debounce.reset()
                self._banner_debounce_after_match_end.reset()
                self._match_end_seen = False
                # このバナーに紐づいてバッファされている可能性のあるゴール検知も、
                # 次の本物の試合に誤って持ち越さないよう破棄する
                self._pending_goals = []
                self._goal_debounce.reset()
                self._goal_recorded_this_event = False
                return None
            self._pending_result = self._banner_candidate
            # Issue #222: バナー確定直後は本来コンパクト表示のはずだが、確定までの
            # 時間が長引くとバッジが既に拡大表示へ遷移していることがあるため、
            # 両方のROIで試す(_run_rank_before_ocr/_apply_rank_before_ocr参照)。
            # Issue #430: 以前はここで読み取りの完了を待っていた(実測2.4〜4.0秒)が、
            # その間にランク変動アニメーションが丸ごと終わってしまい、ゲージ追跡・
            # 降格ラベル検知・手動入力用クリップのどれもアニメーションを取りこぼして
            # いた。投げるだけにして結果は_poll_rank_before_ocr()で受け取る
            self._pending_rank_before_tier = None
            self._pending_rank_before = None
            self._rank_before_match_no = self._session_match_no
            self._rank_before_future = self._rank_ocr_executor.submit(_run_rank_before_ocr, frame)
            self._banner_candidate = None
            self._banner_debounce.reset()
            self._banner_debounce_after_match_end.reset()
            # 「試合終了」の確認は今回の結果バナー確定にのみ使うため、ここでリセットする
            self._match_end_seen = False

            # Issue #235: VS画面のスロット0(自チーム側、自分自身)のランクバッジを
            # 検知できていない試合は「ランクを賭けない対戦」とみなし、ランク変動
            # アニメーションの安定待ち(TRACKING_RANK)を経由せず結果バナー確定時点で
            # 直ちに確定する。ランクを賭けた試合でランク変動アニメーション中に
            # 昇格演出等を挟んで早期確定してしまう不具合(#235本体)とは独立に、
            # 「そもそもランクが無い試合はランクの安定待ち自体が不要」という
            # 前提を先に切り分ける対応(B/C/D/E帯は現状tier=None(未識別)になり
            # 区別できないため、この判定でもランク無しと扱われる。実プレイでは
            # 常に∞帯のためユーザー確認の上、許容する既知の制限)
            if not self.current_match_has_rank:
                # この場合はランク変動の追跡が無くその場で確定するため、
                # 従来どおり読み取りの完了を待つ(待っても取りこぼすものが無い)
                self._poll_rank_before_ocr(wait=True)
                logger.info(
                    "%d試合目: VS画面で自分のランクを検知できなかったため、"
                    "ランクを賭けない試合とみなし結果バナー確定時点で確定します",
                    self._session_match_no,
                )
                return self._finalize(None, None)

            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_started_at = None
            # Issue #396/#430: 帯番号の起点(試合前の帯番号)は、読み取り結果が
            # 届いた時点で_poll_rank_before_ocr()が埋める
            self._grace_candidate_rank_tier = None
            self._latest_gauge_fill = None
            self._pending_gauge_fill = None
            self._pending_gauge_debounce.reset()
            self._last_logged_gauge_fill = None
            self._promotion_confirmed_this_match = False
            self._demotion_confirmed_this_match = False
            self._demotion_label_debounce.reset()
            self._demotion_label_recorded_this_event = False
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._state = _State.TRACKING_RANK
            # 同期的に完了するExecutor(テスト等)ならこの時点で取り込める
            self._poll_rank_before_ocr()
        return None

    def _log_banner_stats(self, frame: np.ndarray, result: BannerResult) -> None:
        """「試合終了」確認後のBANNER_ROIS実測値をDEBUGログに残す(Issue #423)。

        専用部屋の負けバナーが1件も検知されず8試合が丸ごと記録されなかった際、
        「classify_banner()が何を見てNoneを返したのか」を示すデータがログにもDBにも
        残っておらず、原因を特定できなかった(OBSローカル録画では同じ区間が"lose"と
        判定できるため、Virtual Camera経由の映像との色味の差が疑わしいが実測値が無い。
        Issue #373も同種の問題)。閾値の再較正に必要な値をこの区間に限って残す。

        結果バナーが出るのは「試合終了」確認から実測5〜8秒後のため、この区間だけで
        判定に必要な値は揃う。常時出すとDEBUGログのノイズになるので、前回出力時から
        H/S/Vのいずれかが_BANNER_STATS_LOG_TOLERANCEを超えて動いたときだけ出す
        (Issue #384のランクゲージログと同じ考え方)。
        """
        stats = banner_roi_stats(frame)
        if stats is None:
            return
        current = (stats.hue, stats.saturation, stats.value)
        previous = self._last_logged_banner_stats
        if previous is not None and all(
            abs(latest - last_logged) <= _BANNER_STATS_LOG_TOLERANCE
            for latest, last_logged in zip(current, previous)
        ):
            return
        self._last_logged_banner_stats = current
        logger.debug(
            "%d試合目 試合終了後のバナーROI実測: H=%.2f S=%.2f V=%.2f hue_std=%.2f -> %s",
            self._session_match_no,
            stats.hue,
            stats.saturation,
            stats.value,
            stats.hue_std,
            result if result is not None else "判定なし",
        )

    def _track_rank(
        self, frame: np.ndarray, now: float, blackout: Optional[BlackoutObservation] = None
    ) -> Optional[MatchResult]:
        self._check_for_demotion_label(frame, now)

        if is_league_change_screen(frame):
            if self._rank_phase is not _RankPhase.IN_LEAGUE_CHANGE:
                logger.info("%d試合目 リーグ昇格演出を検知しました", self._session_match_no)
            self._rank_phase = _RankPhase.IN_LEAGUE_CHANGE
            self._grace_started_at = None
            self._promotion_confirmed_this_match = True
            return None

        if self._rank_phase is _RankPhase.IN_LEAGUE_CHANGE:
            # 演出が終わった直後。新しいランク値が安定するのを最初から待ち直す
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._rank_phase = _RankPhase.WAITING_STABLE
            return None

        # Issue #209: 暗転(画面全体が真っ黒)を検知したら、GRACE期間の
        # 経過状況に関わらず直ちに確定する。
        # この暗転は試合結果〜ランク確定演出(昇格演出を含む)が完全に終わった
        # 直後にのみ現れるため、候補値を一度でも読み取れていればそれを採用して
        # よい(モジュールdocstring参照)。is_stable系のロジックより前で
        # チェックする必要がある: 暗転自体が直前フレームとの急激な変化になり
        # StabilityMonitorを不安定化させてしまい、素通りするとWAITING_STABLEへ
        # 戻ってこの確定に到達できなくなるため
        # Issue #430: 試合前ランクの読み取りがまだ届いていない間に暗転が来た場合は、
        # ここで完了を待ってから判定する(暗転は既に過ぎているため、待っても
        # 取りこぼすものが無い。待たずに素通りすると帯番号の起点が無いまま
        # GRACE満了まで確定が延びてしまう)
        if (self._grace_candidate_rank_tier is not None or self._rank_before_future is not None) and (
            blackout.blackout if blackout is not None else is_full_blackout(frame)
        ):
            self._poll_rank_before_ocr(wait=True)
            if self._grace_candidate_rank_tier is not None:
                return self._finalize_from_gauge()

        was_stable = self._rank_monitor.is_stable
        is_stable = self._rank_monitor.update(frame)

        if self._rank_phase is _RankPhase.WAITING_STABLE:
            if is_stable and not was_stable:
                self._rank_phase = _RankPhase.GRACE
                self._grace_started_at = now
                # 安定した瞬間(まだ画面が遷移し始めていない良いフレーム)で
                # ゲージの溜まり具合を一度読み、以降の毎フレームデバウンス
                # (_pending_gauge_fill)の起点に揃えておく。
                # Issue #396: 以前はここでread_precise_rank()(帯番号OCR込み、
                # 実測1.5〜1.7秒のブロック)を呼んでいたが、帯番号はGRACE中に
                # 読まない方針へ変更したため、HSVベースの軽量な
                # read_rank_gauge_fill()だけを呼ぶ(モジュールdocstring参照)。
                # TRACKING_RANK中(アニメーション開始後)は常に拡大表示
                initial_fill = read_rank_gauge_fill(frame, GAUGE_ROI_ENLARGED)
                if initial_fill is not None:
                    self._latest_gauge_fill = initial_fill
                    self._pending_gauge_fill = initial_fill
                    self._pending_gauge_debounce.reset()
                    self._pending_gauge_debounce.observe(True, now)
            return None

        # _RankPhase.GRACE: 安定はしたが、直後に昇格/降格演出が始まらないか
        # league_change_grace_seconds秒だけ様子を見る。バナー自体が消えたら
        # 演出は来ないと判断し、猶予期間を待たずに確定してよい
        if not is_stable:
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_started_at = None
            return None

        # Issue #178: ゲージの塗りつぶし(HSVベースの軽量な色判定)は毎フレーム
        # 読み取る。安定判定(StabilityMonitor)のタイミングは、--video実行時の
        # 実時間再生+FfmpegFrameReaderのフレーム間引きの影響でずれることがあり、
        # 確定した瞬間のスナップショットを1回だけ使う方式だと、実際にはまだ
        # 動いている途中の値を掴んでしまうことが実データ(本番DBで誤検知が
        # 見つかったmatches.id=19/20の元動画)で確認された。帯番号は数値OCR
        # (重い処理)のため頻度は変えない。
        #
        # Issue #235: 生値をそのまま_latest_gauge_fillへ反映すると、結果バナー
        # 消灯直後のワイプ演出による一瞬の急騰や、ランクバッジが画面外へ消えて
        # 暗転へフェードしていく過程での急落など、遷移演出由来のノイズも
        # そのまま確定値に混入してしまうことが実データ(2026-08-05実機テスト
        # セッション、3試合目: 負けているのにrank_afterが上昇して記録された)で
        # 判明した。そのため生値を直接は反映せず、banner_confirm_seconds等の
        # 他の検知と同じデバウンスの考え方で、直近rank_recheck_interval_seconds秒分
        # 連続して同じ値(RANK_RECHECK_CHANGE_TOLERANCE許容)が続いて初めて
        # _latest_gauge_fillを更新する。遷移演出中の値は連続一致しないため
        # _latest_gauge_fillへは反映されず、直前の確定値が保持され続ける
        fill = read_rank_gauge_fill(frame, GAUGE_ROI_ENLARGED)
        if fill is not None:
            # Issue #384: 毎フレーム同じ値が流れ続けてDEBUGログのノイズになって
            # いたため、前回ログ出力時の値からRANK_RECHECK_CHANGE_TOLERANCEを
            # 超えて変化した時だけ出力する(他のデバウンス判定と同じ許容誤差)
            if self._last_logged_gauge_fill is None or abs(fill - self._last_logged_gauge_fill) > RANK_RECHECK_CHANGE_TOLERANCE:
                logger.debug(
                    "%d試合目 ランクゲージ: tier=%s fill=%.3f",
                    self._session_match_no,
                    self._grace_candidate_rank_tier,
                    fill,
                )
                self._last_logged_gauge_fill = fill
            gauge_matched = (
                self._pending_gauge_fill is not None and abs(fill - self._pending_gauge_fill) <= RANK_RECHECK_CHANGE_TOLERANCE
            )
            if not gauge_matched:
                self._pending_gauge_fill = fill
                self._pending_gauge_debounce.reset()
            gauge_confirmed = self._pending_gauge_debounce.observe(True, now)
            if gauge_confirmed and (
                self._latest_gauge_fill is None
                or abs(self._pending_gauge_fill - self._latest_gauge_fill) > RANK_RECHECK_CHANGE_TOLERANCE
            ):
                # 確定値が実際に変わった(=まだゲージが動いている途中だった)
                # とみなし、猶予期間をやり直す
                self._grace_started_at = now
            if gauge_confirmed:
                self._latest_gauge_fill = self._pending_gauge_fill

        # ピクセル差分では検知できない緩やかな帯番号の変化を見逃さないよう、
        # 一定間隔で読み直して候補の帯番号が古くなっていないか確認する
        # (ゲージ小数部は上記で毎フレーム追跡済み)。
        if self._grace_started_at is None or (now - self._grace_started_at) < self._league_change_grace_seconds:
            return None
        return self._finalize_from_gauge()

    def _check_for_demotion_label(self, frame: np.ndarray, now: float) -> None:
        """降格ラベル(「降格」の吹き出し)を検知する(Issue #176)。

        is_demotion_label_candidate()(軽量な輝度判定)がdemotion_label_confirm_seconds秒
        連続したタイミングで1回だけconfirm_demotion_label_text()を呼んでOCRで
        確認する(is_goal_event/confirm_goal_textと同じ2段構成、モジュールdocstring
        参照)。確認できれば_demotion_confirmed_this_matchに保持し、_finalize()まで
        持ち越す。
        """
        if not is_demotion_label_candidate(frame):
            self._demotion_label_debounce.reset()
            self._demotion_label_recorded_this_event = False
            return

        if self._demotion_label_debounce.observe(True, now) and not self._demotion_label_recorded_this_event:
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

    def _finalize_from_gauge(self) -> MatchResult:
        """GRACE中に追跡したゲージの溜まり具合と、昇格/降格の独立信号から
        確定値を組み立てて_finalize()する(Issue #396)。

        帯番号(整数部)はGRACE中にOCRしない。試合前の帯番号
        (`_pending_rank_before_tier`、結果バナー確定時に読み取ったもの)を起点に、
        昇格演出(`is_league_change_screen`)・降格ラベル(`confirm_demotion_label_text`)
        という帯番号OCRとは独立した信号でのみ±1する(_infer_tier_after参照)。
        小数部はHSVベースの`read_rank_gauge_fill`をデバウンスした`_latest_gauge_fill`。
        """
        # Issue #430: 試合前の帯番号が無いと推測できないため、読み取りの完了を待つ
        self._poll_rank_before_ocr(wait=True)
        tier_after, rank_after = self._infer_tier_after()
        return self._finalize(tier_after, rank_after)

    def _infer_tier_after(self) -> tuple[Optional[int], Optional[float]]:
        """確定時の帯番号(整数)と、それに小数部を足したランク値を決める(Issue #396)。

        Issue #136で「帯番号OCRが不自然な値を返した場合の最終フォールバック」
        として作った_infer_tier_from_gauge_continuity()を、GRACE中の帯番号OCRを
        廃止したことにより**唯一の決定方法**へ格上げしたもの。判定規則自体は
        当時のまま:

        - 昇格はis_league_change_screen()で独立確認できた場合のみ+1する
        - 降格はconfirm_demotion_label_text()(Issue #176)で独立確認できていれば
          -1する。確認できていない場合は「負けているのにゲージ小数部が
          RANK_TIER_WRAP_MIN_MAGNITUDEを超えて増えて見える(0を割り込んで前の帯へ
          巻き戻ったように見える)」という間接的な判定にフォールバックする
        - それ以外(勝ち・引き分け、または負けでも矛盾がしきい値未満かつ降格ラベル
          未確認)は帯番号を変えず、小数部だけを採用する(ゲージが全く動かない
          引き分けも含め、変な値に書き換えないという方針)

        試合前の帯番号・ランク値が読めていない試合(ランクを賭けない試合等)は
        値を捏造せずNoneのまま返す。
        """
        tier_before = self._pending_rank_before_tier
        rank_before = self._pending_rank_before
        if tier_before is None or rank_before is None:
            return None, None
        if self._latest_gauge_fill is None:
            # ゲージを一度も読めていない。帯番号だけは分かっているが、小数部を
            # 0.0と決めつけると実態とかけ離れた値になるため値は返さない
            # (rank_afterは手動入力で確定させる運用、Issue #305系)
            return tier_before, None

        frac_before = rank_before - tier_before
        frac_after = self._latest_gauge_fill

        if self._promotion_confirmed_this_match:
            return tier_before + 1, tier_before + 1 + frac_after

        if self._pending_result == "lose" and (
            self._demotion_confirmed_this_match or frac_after - frac_before > RANK_TIER_WRAP_MIN_MAGNITUDE
        ):
            return tier_before - 1, tier_before - 1 + frac_after

        return tier_before, tier_before + frac_after

    def _finalize(self, rank_after_tier: Optional[int], rank_after: Optional[float]) -> MatchResult:
        # Issue #189/#397: VS画面OCR(_run_vs_screen_ocr)は別プロセスで実行される。
        # 通常は試合が終わる頃には完了しているはずだが(OCR自体は最大16秒、試合は
        # 数分続く)、念のためここで完了を待ってから_pending_vs_*系フィールドを
        # 読み取る(未完了のままMatchResultを組むと空リストのまま記録されてしまう)
        self._poll_vs_ocr(wait=True)
        # Issue #327: ゴール検知OCR(_run_goal_ocr)も同じ理由で完了を待ってから
        # _pending_goalsを読み取る(_poll_goal_ocr()のdocstring参照)
        self._poll_goal_ocr(wait=True)
        # Issue #430: 試合前ランクも同じ理由で完了を待つ(GRACE満了での確定など、
        # 暗転を経由しない経路でも取りこぼさないため)
        self._poll_rank_before_ocr(wait=True)
        if rank_after is None:
            logger.info(
                "%d試合目: 試合終了時点でもランクバッジを読み取れませんでした"
                "(バッジ非表示、または読み取り失敗の可能性)",
                self._session_match_no,
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

        # Issue #374: 昇格演出/降格ラベルの検知結果は、上記league_changed(数値ベース)
        # とは独立に持ち回る。バッジが完全に読み取れずrank_before/rank_afterが
        # どちらもNoneになる試合でも、帯が変化したこと自体は分かっていることがある
        # ため(database.db側のrank_beforeチェーンの整合性チェックに使う)
        league_change_label_detected = None
        if self._promotion_confirmed_this_match:
            league_change_label_detected = "up"
        elif self._demotion_confirmed_this_match:
            league_change_label_detected = "down"

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
            session_match_no=self._session_match_no,
            league_change_label_detected=league_change_label_detected,
        )
        self._pending_result = None
        self._pending_rank_before = None
        self._pending_rank_before_tier = None
        self._promotion_confirmed_this_match = False
        self._demotion_confirmed_this_match = False
        self._demotion_label_debounce.reset()
        self._demotion_label_recorded_this_event = False
        self._pending_goals = []
        self._goal_debounce.reset()
        self._goal_recorded_this_event = False
        self._pending_vs_mine_ranks = []
        self._pending_vs_opponent_ranks = []
        self._pending_mine_team_color = None
        self._pending_opponent_team_color = None
        self._vs_screen_debounce.reset()
        self._vs_recorded_this_match = False
        self._vs_confirmed_this_match = False
        # Issue #234: 通常は試合の長さ(数分)に対しロック時間(既定30秒)は
        # 十分短く自然に経過しているはずだが、念のため試合終了時点で
        # 明示的に解除し、次の試合の本物のVS画面検知を妨げないようにする
        self._vs_lockout_until = None
        # Issue #190: 「試合終了」バナーをOCRで確認できた試合に限りOBSシーン切替
        # (in_match=False)を行う。確認できなかった試合(実プレイ中の背景誤検知が
        # banner_confirm_secondsを突破した可能性を否定できない)はin_matchをTrueの
        # ままにし、試合中シーンに留める。MatchResultの記録自体はこの確認結果に
        # 関わらず常に行う(モジュールdocstring参照)。
        # Issue #224: in_matchを即座にFalseにはせず、暗転検知から一定時間後に
        # 切り替える(_check_pending_obs_switch参照)。ランク値の記録タイミング自体は
        # 変更しない。
        # Issue #371: _pending_obs_switchを立てる場所自体は_check_for_match_end()へ
        # 移した(ここでは既に立っている)。ここに残っているのは、「試合終了」を
        # 確認できないまま試合が終わった場合に気づけるようにするログのみ
        if not self._match_end_confirmed_this_match:
            logger.info(
                "%d試合目: 「試合終了」バナーを確認できなかったためOBSシーン切替を見送ります"
                "(試合中シーンのまま維持、次に確認できた試合まで持ち越します)",
                self._session_match_no,
            )
        self._match_end_confirmed_this_match = False
        self._banner_absence_debounce.reset()
        self._state = _State.COOLDOWN
        return match_result

    def _watch_for_banner_absence(self, frame: np.ndarray, now: float) -> Optional[MatchResult]:
        if self._banner_absence_debounce.observe(classify_banner(frame) is None, now):
            self._banner_absence_debounce.reset()
            self._state = _State.WATCHING
        return None
