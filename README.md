# Sales Analytics Agent on Cloud Run

「10月の売上は、9月と比べてどうでしたか？」という質問を 1 つだけ投げるデモです。
ADK の Root Agent が Conversational Analytics の Data Agent に分析を頼み、BigQuery で集計して答えます。Cloud Run の非公開 service として動かします。

## データと、答えが変わる理由

架空の注文 88 件（4 列）を使います。注文の状態は 3 つあります。

| status | 意味 |
|---|---|
| 受注 | 注文は受けたが、まだ検収されていない |
| 検収済 | 検収が済んだ。この会社はここで売上を計上する |
| 取消 | キャンセルされた |

どの注文を「売上」に数えるかで、同じ質問の答えが増加にも減少にもなります。

| 数えた注文 | 9 月 | 10 月 | 増減 |
|---|---:|---:|---:|
| 検収済だけ（この会社の「売上」） | 12,000,000 円 | 10,560,000 円 | **−12.0%** |
| 受注＋検収済 | 12,600,000 円 | 13,530,000 円 | **+7.4%** |
| 全部 | 13,500,000 円 | 14,190,000 円 | +5.1% |

## 2 つの条件

同じ質問を、業務定義なし・ありで実行できます。app 名が条件を選びます。

| app 名 | 業務定義 |
|---|---|
| `sales_raw` | なし |
| `sales_defined` | あり |

- `sales_raw` は元の `orders` を、`sales_defined` は同じ 88 件を複製した `orders_glossary` を参照します。
- セットアップ時に Knowledge Catalog のビジネス用語集 `sales-demo-glossary` に「確定売上」を登録し、複製側のテーブルだけに関連付けます。既存の用語の定義は保持します。
- 両方の Data Agent の指示文は同じです。BigQuery の定義あり側の Data Agent を開き、「用語集」で「確定売上」と関連先の `orders_glossary` を確認できます。
- 定義あり側の Root Agent は起動時に同じ用語の説明を読み、指示に加えます。トレースと応答には、その時点で Root Agent が読んだ定義のハッシュが残ります。
- どちらの条件でも、Data Agent への依頼は 1 問につき 1 回です。回答はその分析結果から書き、根拠の検査も同じ結果に対して行います

## 構成

```text
client -> Cloud Run: agent (private, ID token)
            |-- Catalog                       業務定義（確定売上）。起動時に読む
            |-- Gemini (Vertex AI)            Root Agent のモデル
            |-- Data Agent (raw / defined)    分析。SQL を書いて実行する
            |     `-- BigQuery                条件に対応する注文テーブル
            `-- Cloud Trace                   model / tool / Data Agent 呼び出し単位
```

- `sales/root.py`: Root Agent。条件ごとに 1 つ作る
- `sales/catalog.py`: ビジネス用語の作成・読み取りとテーブルへの関連付け
- `sales/data_agent.py`: Data Agent の定義、作成、質問
- `sales/data.py`: データと BigQuery への投入 SQL
- `agents/sales_*/agent.py`: ADK の app
- `server.py`: ADK API server。`/` で比較画面も返す
- `web/index.html`: 比較画面（1 ファイル、build なし）
- `scripts/`: 準備（`provision.py`）、deploy、定義の反映（`sync_definition.py`）、実行と trace、手元からの転送（`proxy.py`）、片付け（`cleanup.py`）
- `semantics/`: 共通の指示と、用語を最初に登録するときの本文

## 準備

Python 3.11 以上、gcloud、[Agents CLI](https://google.github.io/agents-cli/) が必要です。課金が有効な Google Cloud project を 1 つ用意し、その owner で実行してください。既定では BigQuery と Catalog を US に作成します。場所を変更する場合は、BigQuery テーブルと用語集を同じロケーションにしてください。

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
python scripts/provision.py     # API 有効化、dataset、注文テーブルと複製、用語と関連付け、Data Agent 2 つ
./scripts/deploy.sh             # agent service を Cloud Run に deploy
```

- どのコマンドも `PROJECT_ID` の project だけを対象にします。gcloud の既定 project は使いません
- `provision.py` は、自分が作っていない `orders` テーブルには触りません。同じ名前のテーブルがすでにあると、何も変えずに止まります。そのときは `BQ_DATASET` で別の dataset を指定してください。自分が作ったテーブルを作り直すときだけ `--recreate-table` を付けます
- `provision.py` は最後にテーブルの行数（88 行）を確かめます。足りないときは、`--recreate-table` を付けて実行し直すよう表示して止まります
- 既存の Data Agent は、指示文と参照するテーブルの両方が今回の設定と同じときだけ使い回します。同じ project で `BQ_DATASET` を変えて実行すると、以前の dataset を見ている Data Agent が残っているので、対象を表示して止まります。`cleanup.py` で消すか、`DATA_AGENT_ID_RAW`・`DATA_AGENT_ID_DEFINED` で別の名前を指定してください
- `agents-cli` は gcloud の login より Application Default Credentials を優先します。`gcloud auth application-default login` で deploy 先と同じ account にそろえてください
- agent service は Compute Engine の既定の service account で動きます。この account が Gemini（Vertex AI）、Data Agent、BigQuery、Catalog の読み取り、Cloud Trace への書き込みを行えることが必要です。別の account で動かすときは `AGENT_SA` で指定します

## 実行

```bash
export SERVICE_URL="$(gcloud run services describe sales-analytics-agent --project "$PROJECT_ID" --region asia-northeast1 --format='value(status.url)')"
```

### 比較画面

agent service は非公開なので、手元で identity token を付けて転送します。

```bash
python scripts/proxy.py
```

ブラウザで `http://localhost:8790/` を開くと、2 条件を横に並べた画面が出ます。
「① → ② の順に実行」で同じ質問を順に送り、回答、所要時間、Data Agent の分析時間、token 数、実行された SQL を見比べられます。各列の「Cloud Trace でこの実行を開く」から、その実行の trace に飛べます。

### コマンドから

```bash
python scripts/ask.py sales_raw sales_defined   # 同じ質問を 2 条件で
python scripts/trace.py 3                       # 直近 3 回の Cloud Trace
```

`sales_defined` の trace には、Root Agent が起動時に読んだ定義が `sales.definition_entry`、`sales.definition_update_time`、`sales.definition_sha256` として、問い合わせた Data Agent が `sales.data_agent` として残ります。

## 定義を変えてみる

Knowledge Catalog の用語集 `sales-demo-glossary` で「確定売上」の説明を編集します。定義あり側の Data Agent の「用語集」で変更が反映されたことを確認し、次を実行して Root Agent にも読み直させます。

```bash
python scripts/sync_definition.py
```

用語の反映と Cloud Run の revision 切り替えには時間差があります。比較実験は両方に新しい定義が反映された後に実行してください。

旧版から更新する場合は `provision.py` を再実行してから再デプロイしてください。以前のカスタムエントリを編集していた場合は、その内容を新しい用語の説明に反映してください。

## 片付け

```bash
python scripts/cleanup.py          # 削除するものを表示
python scripts/cleanup.py --yes    # 削除
gcloud run services delete sales-analytics-agent --project "$PROJECT_ID" --region asia-northeast1
```

削除予定の一覧にはデモの Data Agent、用語とテーブルの関連付け、用語、用語集、ラベル付きの注文テーブル 2 つが表示されます。旧版で作成したデモ用のカスタムエントリも対象です。dataset は残ります。

## 費用と注意

- Conversational Analytics の Data Agent は 2026 年 12 月 31 日まで無料試用期間です。それ以降の料金は公式ページを確認してください
- Gemini、BigQuery の query、Cloud Run、Cloud Trace、Cloud Build には別途料金がかかります
- session は instance の memory に置いています。`SESSION_SERVICE_URI` を指定しない限り、instance が入れ替わると消えます
- 使い終わったら「片付け」の手順で削除してください
- データと数値は説明用の架空のものです

## テスト

```bash
pip install -r requirements.txt -c constraints.txt pytest
pytest -q
```

Google Cloud には接続しません。手計算の合計、条件ごとの定義の有無、定義が Catalog から渡ること、用語の関連先が定義あり側のテーブルであること、既存の Data Agent を指示文と参照先が同じときだけ使い回すこと、Data Agent への依頼が 1 問につき 1 回であることを確かめます。

## 用語集を使った検証（2026-11-28）

発表用の実プロジェクトでは `orders` の複製 `orders_glossary_trial` にだけ用語を関連付けました。本サンプルの複製名は `orders_glossary` です。定義ありの条件で同じ質問を新しいセッションから 5 回実行し、5 回とも SQL は検収済みの注文だけを集計しました。9 月は 1,200 万円、10 月は 1,056 万円（12.0% 減）です。

## 参考：指示文で定義を渡していた旧実装の計測（2026-11-21）

実 project で、2 条件を交互に 5 回ずつ実行した結果です。回答は実行のたびに変わることがあります。この計測は、定義の版ごとに Data Agent を分ける変更の 1 つ前の commit（`c59831e`）で行いました。変更後は、定義の書き換え・反映・切り戻しで回答と版が入れ替わることだけを確かめています。

| 条件 | 回答（5 回） | 全体 | うち Data Agent |
|---|---|---|---|
| `sales_raw` | 検収済だけを合計し −12.0% が 3 回、取消以外を合計し +7.4% が 2 回 | 28.4〜37.2 秒が 4 回、59.2 秒が 1 回 | 11.1〜17.0 秒 |
| `sales_defined` | 検収済だけを合計し −12.0% が 5 回 | 21.7 秒が 1 回、58.1〜94.8 秒が 4 回 | 9.7〜11.5 秒 |

- `sales_defined` の SQL は 5 回とも `WHERE status = '検収済'` でした。Trace に残った定義の版（`sales.definition_sha256`）は 5 回とも `4e3bb8903c6d` です
- 全体が 58 秒を超えた 5 回は、Root Agent の最初の model 呼び出しが 29.7〜76.3 秒でした（Cloud Trace で確認）。最も長い 94.8 秒の回でも、Data Agent は 11.2 秒です。この待ちは速い時間帯を選ばずに測った結果で、条件の違いによるものとは確認していません

データ構成の出発点は [Data & AI Summit '26 Fall ハンズオン](https://github.com/mbk-digital/data-ai-summit-2026-fall-handson)（Apache License 2.0）です。
