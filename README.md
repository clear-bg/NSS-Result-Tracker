# Switch Sports サッカー 試合記録・分析システム

Nintendo Switch Sports「サッカー」のプレイ映像をキャプチャーボード経由でリアルタイム解析し、勝敗・ランク変動を自動記録するシステム。将来的には配信画面へのグラフ表示までを行う。

## 概要

- キャプチャーボードで取り込んだ映像を解析し、試合の勝敗とランク(∞帯)の変動を自動検知・記録する
- 配信しながら裏側で常時記録し続けられることを前提に設計する
- 蓄積したデータを使い、配信画面にグラフなどを表示する

## 現在のスコープ(第一段階)

- [x] 勝敗の自動記録
- [x] ランク(数値・ゲージ)の自動記録
- [ ] ゴール・アシストの記録(将来段階)
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

## データ設計(ドラフト、未確定)

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,      -- 結果バナー検知時刻(ISO8601)
    result TEXT NOT NULL,           -- 'win' or 'lose'
    rank_before INTEGER,            -- 結果バナー表示時点のランク値
    rank_after INTEGER,             -- ランク変動確定後の値
    league_changed TEXT             -- 'up' / 'down' / NULL
);
```

<!-- TODO: フィールドの過不足、型、インデックス設計はClaude Codeでの実装着手時に確定 -->

## セットアップ

依存関係管理には [uv](https://docs.astral.sh/uv/) を使用する。

```bash
# uv自体のインストール(Windows PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 依存関係のインストール(pyproject.toml / uv.lock から環境を再現)
uv sync

# 実行例(仮想環境のactivateは不要)
uv run python main.py
```

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
│   └── screenshots/          # 状態ごとの参照画像(.gitignore対象、ローカルのみ)
├── scripts/                   # 手動実行の診断・検証用スクリプト
├── src/
│   └── nss_tracker/
│       ├── capture/            # ffmpeg+dshowによる継続フレーム取得
│       ├── detection/          # banner(勝敗判定) / rank_ocr(ランクOCR) / motion(状態監視)
│       ├── state/              # 試合の状態遷移管理
│       ├── storage/            # SQLite読み書き
│       └── web/                # 将来のグラフ表示用(未実装)
└── tests/                      # pytest(fixtures/screenshotsを使った検知ロジックのテスト)
```

## ステータス

現在: 方針・技術スタック検討フェーズ。実装は Claude Code で行う。
