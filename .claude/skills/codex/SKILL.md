---
name: codex
description: OpenAI Codex にセカンドオピニオンを求める。「/codex review」でコード差分のレビュー、「/codex check <file>」で資料のファクトチェック、「/codex ask <質問>」で相談、「/codex rescue <指示>」で修正の代行。Claude が作った成果物を別モデルでクロスチェックしたいときに使う。
argument-hint: review [--base BRANCH|--uncommitted] | check FILE | ask "質問" | rescue "指示"
---

# Codex 連携

Claude Code が作成 → Codex がレビュー → Claude が反映、の流れで品質を上げるためのスキル。
実体は `scripts/codex.sh`（Codex CLI の `codex exec` ラッパー）。

## 手順

1. 引数（`$ARGUMENTS`）の先頭をサブコマンドとして、次をそのまま実行する。
   長くかかるので Bash の timeout は 600000 を指定する。

   ```bash
   bash scripts/codex.sh $ARGUMENTS
   ```

   | サブコマンド | 用途 | Codex の権限 |
   |---|---|---|
   | `review` | 差分のコードレビュー（未コミットがあればそれ、なければ default branch との差分） | 読み取りのみ |
   | `check FILE [観点]` | .md 等の資料のファクトチェック・論理チェック | 読み取りのみ |
   | `ask "質問"` | 設計判断などの相談 | 読み取りのみ |
   | `rescue "指示"` | 行き詰まった修正を Codex に任せる | 作業ディレクトリへの書き込み可 |

   引数がなければ `review` として扱う。

2. 結果を鵜呑みにしない。各指摘を実際のコード・資料で検証し、
   「採用 / 不採用（理由）」に仕分ける。

3. 採用した指摘を Claude 自身が修正する（`rescue` の場合は Codex の変更を `git diff` で確認する）。

4. ユーザーへの報告は次の 3 項目で行う。
   - ① 初稿の概要
   - ② Codex のレビュー結果（採用・不採用とその理由）
   - ③ 修正反映後の最終成果物（ファイルパス）

## うまく動かないとき

- `OPENAI_API_KEY が設定されていません` → クラウド環境の設定で環境変数 `OPENAI_API_KEY` を追加してもらう。キーをチャットに貼らせない。
- `403` / 接続エラー → 環境のネットワーク設定で `api.openai.com` を許可してもらう。
- それ以外のエラーは出力をそのままユーザーに伝える。Codex が使えない場合、勝手に別手段で「Codex レビュー済み」と報告しない。
