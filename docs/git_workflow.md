# Git / GitHub 運用ルール(ドラフト)

個人開発想定の最低限のルール。まだ確定していない箇所は `<!-- TODO -->` を付けてあるので、運用しながら決めていく。

## ブランチ運用

- `main`: 常に動く状態を保つ
- 作業用ブランチ: `feature/xxx`、`fix/xxx` のように接頭辞をつける
  - 例: `feature/rank-ocr`, `fix/banner-false-positive`

<!-- TODO: 個人開発でPRを挟むか、mainに直接コミットするかを決める -->

## コミットメッセージ

[Conventional Commits](https://www.conventionalcommits.org/) 形式を採用する。

```
<type>: <説明>

例:
feat: 結果バナーの色判定ロジックを追加
fix: ランクOCRが暗転時に誤検知する問題を修正
docs: CLAUDE.mdに検知方式の方針を追記
chore: 依存パッケージを更新
```

主な `type`:

| type | 用途 |
|---|---|
| feat | 新機能 |
| fix | バグ修正 |
| docs | ドキュメントのみの変更 |
| refactor | 挙動を変えない内部整理 |
| test | テストの追加・修正 |
| chore | ビルド設定・依存関係など |

## Issue / タスク管理

<!-- TODO: GitHub Issuesを使うか、他の手段(Obsidianのメモ等)で管理するかを決める -->

## .gitignore の方針

以下は含めない(リモートにpushしない)想定:

- 実機キャプチャの生録画データ(検証用の短いクリップを除く)
- 仮想環境ディレクトリ(`.venv/` など)
- `__pycache__/`, `*.pyc`
- ローカルのSQLiteデータファイル本体(スキーマやマイグレーションは含めるが、実データは含めない)
- **`fixtures/screenshots/` 配下の参照画像一式**
  - 理由: 他のプレイヤーの見た目やプレイヤー名がそのまま映り込んでいるため、プライバシー配慮でリモートには一切上げない。ローカル環境にのみ置く運用とする
  - `docs/screen_states.md` に画像のファイル名一覧(=期待される命名規則)は記載済みなので、リポジトリをクローンした環境では各自ローカルで画像を配置する
  - 空フォルダの構成だけは把握できるよう、`fixtures/screenshots/.gitkeep` など中身のないファイルのみ追跡する

<!-- TODO: 依存関係管理ツール確定後、requirements.txt / poetry.lock / uv.lock のいずれを追跡するか明記する -->

## リリース・タグ

<!-- TODO: バージョニング方針(意識するか、個人開発なので不要とするか)を決める -->
