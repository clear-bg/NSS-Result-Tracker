# Switch Sports サッカー 試合記録・分析システム

Nintendo Switch Sports「サッカー」のプレイ映像をキャプチャーボード経由でリアルタイム解析し、勝敗・ランク変動を自動記録するシステム。将来的には配信画面へのグラフ表示までを行う。

## 概要

- キャプチャーボードで取り込んだ映像を解析し、試合の勝敗とランク(∞帯)の変動を自動検知・記録する
- 配信しながら裏側で常時記録し続けられることを前提に設計する
- 蓄積したデータを使い、配信画面にグラフなどを表示する

## 現在のスコープ(第一段階)

- [x] 勝敗(勝ち・負け・引き分け)の自動記録
- [x] ランク(数値・ゲージ)の自動記録、リーグ昇格・降格の検知
- [x] ゴール・アシスト(得点者・アシスト者名)の記録(得点者・アシスト者のどちらか一方でもプレイヤー許可リストにあれば記録)
- [x] マッチング完了(VS画面)〜試合終了までを1試合として捕捉、VS画面での対戦相手ランクの記録
- [x] 配信画面向けWebダッシュボード(勝率・ランク推移グラフ・ゴール/アシスト統計等)
- [x] obs-websocket連携によるOBSシーンの自動切り替え(試合中/試合間)
- [ ] 配信画面へのグラフ表示(将来段階)

## 環境

| 項目 | 内容 |
| --- | --- |
| OS | Windows |
| キャプチャーボード | I-O DATA GV-USB3HDS/E(2K120pパススルー・録画対応) |
| キャプチャ解像度 | 1920x1080 を想定 |
| 配信ソフト | OBS Studio |

## アーキテクチャ概要

```txt
Switch(有線コントローラー操作) → キャプチャーボード(HDMI IN)
                                     │
                                     ├─ HDMI OUT(パススルー) → プレイ用モニター
                                     └─ USB → PC
                                              │
                                            OBS Studio(配信・録画)
                                              │
                                        OBS Virtual Camera
                                              │
                                    Python 解析プロセス(常時起動)
                                      ├─ 状態監視(軽量・高頻度)
                                      ├─ OCR/判定(状態確定時のみ)
                                      ├─ SQLite への記録
                                      ├─ ローカルWebサーバー(グラフ配信)
                                      └─ obs-websocket(シーン自動切替)
                                              │                  │
                                    OBS ブラウザソース         OBS シーン切替
                                    (配信画面に重畳)           (試合中/試合間)
```

補足:

- キャプチャーボードは同時に1アプリしか掴めない想定のため、Python側は物理デバイスではなく OBS の Virtual Camera を映像ソースとして利用する
- 「軽量な状態監視」はキャプチャの生フレームレート(30fps想定)でそのまま回して問題ない。OCRなど重い処理は状態が確定したタイミングでのみ実行する

## 試合の状態遷移(現時点の設計)

1. マッチング完了(VS画面、両チームのランク数値を記録)〜プレイ中(ゴール・アシストを検知)
2. 結果バナー表示(勝敗・開始時ランクを記録)
3. ランク変動アニメーション(数値が安定するまで監視)
4. ランク確定(最終値、リーグ昇格/降格の有無を記録)
5. 暗転
6. マッチング画面(クールダウン解除、次の試合へ)

詳細な画面状態の一覧は [`docs/screen_states.md`](docs/screen_states.md) を参照。

## データ設計

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,      -- 結果バナー検知時刻(ISO8601, JST)
    result TEXT NOT NULL,           -- 'win' / 'lose' / 'draw'
    rank_before REAL,               -- 結果バナー表示時点のランク値(帯番号+ゲージ溜まり具合の小数値。表示時に丸める)
    rank_after REAL,                -- ランク変動確定後の値(同上)
    league_changed TEXT,            -- 'up' / 'down' / NULL
    created_at TEXT NOT NULL,       -- レコード作成時刻(ISO8601, JST)
    updated_at TEXT NOT NULL        -- レコード最終更新時刻(ISO8601, JST)
);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    detected_at TEXT NOT NULL,      -- ゴール検知時刻(ISO8601, JST)
    scorer_name TEXT NOT NULL,
    assist_name TEXT,               -- アシスト無しの場合NULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE vs_slot_ranks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    side TEXT NOT NULL,             -- 'mine' / 'opponent'
    slot_index INTEGER NOT NULL,    -- 0(カメラに最も近い位置)〜3(最も奥)
    rank_tier INTEGER,              -- 読み取れなかった場合(文字階級バッジ・非表示含む)NULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- `detected_at`は試合結果/ゴールを検知した実時刻(期間で絞り込む集計・グラフ表示等に使う)、`created_at`/`updated_at`はレコード自体の作成・更新時刻(監査用)。今後追加するテーブルにも`created_at`/`updated_at`は同様に持たせる
- 全ての日時カラムはJST(日本標準時、`src/nss_tracker/timeutil.py`の`now_jst()`)で保存する。個人利用(日本国内)のみを想定しており、UTCで保存すると目視確認時に9時間ズレて分かりにくいため。ログ(`logs/tracker.log`)のタイムスタンプもJSTで統一している
- `result`には引き分け(`draw`)も含まれる(ランクを賭けない対戦限定、`src/nss_tracker/detection/banner.py`参照)
- `goals`は`matches.id`を`match_id`として参照する。得点者・アシスト者のどちらもプレイヤー許可リスト(`.env`の`ALLOWED_PLAYERS`)に無い場合、そのゴールは保存されない。どちらか一方でも許可されていれば、もう一方が許可リスト外の名前でもそのまま保存する
- `vs_slot_ranks`は、マッチング完了直後のVS画面で読み取った両チーム最大4人分の∞帯ランク数値を1行=1スロット(最大1試合あたり8行)で保存する。名前を持たない(数値のみの)テーブルのため、`goals`と異なり許可リストによるフィルタリングは行わない。VS画面を見逃した試合では行自体が保存されない
- 実装は`src/nss_tracker/database/db.py`を参照

## セットアップ

依存関係管理には [uv](https://docs.astral.sh/uv/) を使用する。

```bash
# uv自体のインストール(Windows PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 依存関係のインストール(pyproject.toml / uv.lock から環境を再現)
uv sync

# 実行例(仮想環境のactivateは不要。OBS Virtual Cameraからの実キャプチャ)
uv run python main.py

# OBS/Switchを用意していない段階でも配線を確認したい場合、
# 動画ファイルをOBS Virtual Cameraの代わりに読み込める
uv run python main.py --video fixtures/videos/01_win_blue_2-1.mp4
```

実行前に、`.env.example`をコピーして`.env`を作成し、値を設定する(`.env`は`.gitignore`対象、他プレイヤーの実名を含みうるため)。

- `ALLOWED_PLAYERS`: 得点・アシストを記録する対象のプレイヤー名(カンマ区切り)。空文字でもよい(その場合ゴール・アシストは記録されない)
- `CAPTURE_DEVICE_NAME` / `CAPTURE_WIDTH` / `CAPTURE_HEIGHT`: キャプチャデバイス名・解像度(`--video`未指定の実キャプチャ時に必須。未設定の場合は起動時にエラーになる)
- `OBS_WEBSOCKET_HOST` / `OBS_WEBSOCKET_PORT` / `OBS_WEBSOCKET_PASSWORD` / `OBS_SCENE_IN_MATCH` / `OBS_SCENE_BETWEEN_MATCHES`: OBSシーン自動切り替え(下記)の接続設定・切替先シーン名

検知処理(勝敗バナー・ランクOCR・ゴール検知・VS画面検知等)のROI・色閾値は`config/detection.toml`で管理している。モニターやキャプチャボードの発色特性の違いで検知精度がズレる場合は、このファイルの該当する値を直接書き換えればよい(キー・ファイル自体が無い場合はコード側のデフォルト値にフォールバックする)。

### OBS Virtual Cameraのセットアップ(初回のみ)

実機キャプチャ(`--video`オプション無しでの実行)には、事前にOBS Studio側の設定が必要。

1. Switchとキャプチャーボード(I-O DATA GV-USB3HDS/E)を接続し、いつも通りプレイできる状態にする
2. OBS Studioを起動し、キャプチャーボードの映像をソースとして追加する(通常の配信・録画設定と同じでよい)
3. OBSの映像設定(基本解像度・出力解像度)を**1920x1080**に設定する
   - コードは1920x1080を前提に生バイト列を画像として組み立てているため、OBS側の解像度と一致していないと映像が壊れる。1920x1080以外で運用する場合は`.env`の`CAPTURE_WIDTH`/`CAPTURE_HEIGHT`をOBS側の設定と一致させること
4. OBSの「仮想カメラを開始」ボタンを押す
   - これを押さないと、ffmpegが掴もうとする`"OBS Virtual Camera"`というdshowデバイス自体が存在しない状態になる

設定後、`uv run python main.py`を実行する。疎通確認だけ先に行いたい場合は[`docs/capture_verification.md`](docs/capture_verification.md)を参照。

### OBSシーン自動切り替えのセットアップ(任意)

試合状態(試合中/試合間)に応じてOBSのシーンを自動切り替えたい場合、以下を設定する。

1. OBSの「ツール」メニュー→「WebSocketサーバー設定」で、obs-websocketを有効化する(OBS 28以降は標準搭載)。ホスト・ポート・パスワードを`.env`の`OBS_WEBSOCKET_HOST`/`OBS_WEBSOCKET_PORT`/`OBS_WEBSOCKET_PASSWORD`に設定する(認証を無効化している場合は`OBS_WEBSOCKET_PASSWORD`を空欄のままにする)
2. OBS側に「試合中(ゲーム画面全画面)」「試合間(ワイプ+ダッシュボード)」用のシーンをそれぞれ作成し、そのシーン名を`.env`の`OBS_SCENE_IN_MATCH`/`OBS_SCENE_BETWEEN_MATCHES`に設定する

OBSが未起動・obs-websocketが無効・パスワード不一致等で接続に失敗しても、検知・DB記録などの本来の機能には影響しない(WARNINGログを出したままシーン自動切替のみ無効化されて起動を継続する)。

## フォルダ構成

```txt
.
├── README.md
├── CLAUDE.md
├── config/
│   └── detection.toml         # detection/のROI・色閾値設定(デフォルト値入り、上書き自由)
├── docs/
│   ├── screen_states.md      # 画面状態の一覧(スクショ対象.mdより)
│   └── git_workflow.md       # Git/GitHub運用ルール
├── fixtures/
│   ├── screenshots/          # 状態ごとの参照画像(.gitignore対象、ローカルのみ)
│   └── videos/               # 状態遷移確認用の参照動画(.gitignore対象、ローカルのみ)
├── scripts/                   # 手動実行の診断・検証用スクリプト
├── src/
│   └── nss_tracker/
│       ├── capture/            # ffmpeg+dshowによる継続フレーム取得
│       ├── detection/          # banner(勝敗判定) / rank_ocr(ランクOCR) / motion(状態監視) /
│       │                       # league_change(リーグ変更) / goal(ゴール・アシスト検知) /
│       │                       # matchmaking(VS画面検知) / vs_rank(VS画面ランクOCR)
│       ├── detection_config.py # config/detection.tomlの読み込み(ROI・色閾値)
│       ├── state/              # 試合の状態遷移管理
│       ├── database/           # SQLite読み書き
│       ├── obs_control.py      # obs-websocket経由のOBSシーン自動切り替え
│       └── web/                # 配信画面向けWebダッシュボード
└── tests/                      # pytest(fixtures/screenshotsを使った検知ロジックのテスト)
```

## ステータス

現在: capture(ffmpeg+dshow) → detection → state → database の一連の配線を`main.py`で実装済み。動画ファイルを入力に差し替えた動作確認(`--video`オプション)・OBS Virtual Camera実機での疎通確認(`docs/capture_verification.md`参照)ともに完了。実際にSwitchをプレイしてのend-to-end動作確認(banner検知・ランクOCR・ゴール検知・リーグ昇格/降格判定)も完了しており、SQLiteへの記録まで一通り動作する。

既知の精度課題: ゴール演出中に結果バナーが誤検知されるケース、VS画面のロゴ検知が実プレイでは不安定なケースが見つかっており、追跡・改善中。
