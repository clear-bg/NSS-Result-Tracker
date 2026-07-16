# Switch Sports サッカー 試合記録・分析システム

Nintendo Switch Sports「サッカー」のプレイ映像をキャプチャーボード経由でリアルタイム解析し、勝敗・ランク変動を自動記録するシステム。将来的には配信画面へのグラフ表示までを行う。

## 概要

- キャプチャーボードで取り込んだ映像を解析し、試合の勝敗とランク(∞帯)の変動を自動検知・記録する
- 配信しながら裏側で常時記録し続けられることを前提に設計する
- 蓄積したデータを使い、配信画面にグラフなどを表示する

## 現在のスコープ(第一段階)

- [x] 勝敗の自動記録
- [x] ランク(数値・ゲージ)の自動記録
- [x] ゴール・アシスト(得点者・アシスト者名)の記録(プレイヤー許可リストにある得点者のみ)
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
                                      └─ ローカルWebサーバー(グラフ配信)
                                              │
                                    OBS ブラウザソース(配信画面に重畳)
```

補足:

- キャプチャーボードは同時に1アプリしか掴めない想定のため、Python側は物理デバイスではなく OBS の Virtual Camera を映像ソースとして利用する
- 「軽量な状態監視」はキャプチャの生フレームレート(30fps想定)でそのまま回して問題ない。OCRなど重い処理は状態が確定したタイミングでのみ実行する

## 試合後の状態遷移(現時点の設計)

1. 結果バナー表示(勝敗・開始時ランクを記録)
2. ランク変動アニメーション(数値が安定するまで監視)
3. ランク確定(最終値、リーグ昇格/降格の有無を記録)
4. 暗転
5. マッチング画面(クールダウン解除、次の試合へ)

詳細な画面状態の一覧は [`docs/screen_states.md`](docs/screen_states.md) を参照。

## データ設計

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,      -- 結果バナー検知時刻(ISO8601)
    result TEXT NOT NULL,           -- 'win' / 'lose' / 'draw'
    rank_before REAL,               -- 結果バナー表示時点のランク値(帯番号+ゲージ溜まり具合の小数値。表示時に丸める)
    rank_after REAL,                -- ランク変動確定後の値(同上)
    league_changed TEXT,            -- 'up' / 'down' / NULL
    created_at TEXT NOT NULL,       -- レコード作成時刻(ISO8601)
    updated_at TEXT NOT NULL        -- レコード最終更新時刻(ISO8601)
);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    detected_at TEXT NOT NULL,      -- ゴール検知時刻(ISO8601)
    scorer_name TEXT NOT NULL,
    assist_name TEXT,               -- アシスト無しの場合NULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- `detected_at`は試合結果/ゴールを検知した実時刻(期間で絞り込む集計・グラフ表示等に使う)、`created_at`/`updated_at`はレコード自体の作成・更新時刻(監査用)。今後追加するテーブルにも`created_at`/`updated_at`は同様に持たせる
- `result`には引き分け(`draw`)も将来含まれる想定(現時点では検知未実装)
- `goals`は`matches.id`を`match_id`として参照する。得点者がプレイヤー許可リスト(`.env`の`ALLOWED_PLAYERS`)に無い場合、そのゴールは保存されない
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

得点・アシストを記録する場合は、`.env.example`をコピーして`.env`を作成し、`ALLOWED_PLAYERS`に記録対象のプレイヤー名(カンマ区切り)を設定する。`.env`は`.gitignore`対象(他プレイヤーの実名を含みうるため)。

<!-- TODO: 初回セットアップ手順(OBS設定、Virtual Camera有効化手順など)を記載 -->

## フォルダ構成

```txt
.
├── README.md
├── CLAUDE.md
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
│       ├── detection/          # banner(勝敗判定) / rank_ocr(ランクOCR) / motion(状態監視)
│       ├── state/              # 試合の状態遷移管理
│       ├── database/           # SQLite読み書き
│       └── web/                # 将来のグラフ表示用(未実装)
└── tests/                      # pytest(fixtures/screenshotsを使った検知ロジックのテスト)
```

## ステータス

現在: capture(ffmpeg+dshow) → detection → state → database の一連の配線を`main.py`で実装済み。動画ファイルを入力に差し替えた動作確認は完了(`--video`オプション)。OBS Virtual Camera実機での疎通確認は未実施(`docs/capture_verification.md`参照)。
