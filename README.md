# Sales Analytics Agent on Cloud Run

「10月の売上は、9月と比べてどうでしたか？」という質問を 1 つだけ投げるデモです。
ADK の Root Agent が Conversational Analytics の Data Agent に分析を頼み、BigQuery で集計して答えます。Cloud Run の非公開 service として動かします。

## データと、答えが変わる理由

テーブルは `orders` の 1 つだけです（88 行、4 列）。注文の状態は 3 つあります。

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

- 2 条件とも同じ `orders` テーブルを参照します
- 業務定義（4 行）は Catalog のエントリ `sales-demo/confirmed-sales`（確定売上）に置き、そこから読みます。有無は Data Agent と Root Agent の両方でそろえています
- Root Agent は起動時に Catalog から定義を読み、どのエントリの、いつ更新された版を使ったかをトレース（`sales.definition_*`）と応答（`definition`）に残します
- Data Agent は定義の写しを自分で持ちます。定義の版（本文の sha256 の先頭 12 桁）ごとに別の Data Agent（`sales-defined-<版>`）を作り、作ったものは書き換えません。Root Agent は、自分が読んだ版の Data Agent にだけ問い合わせます。エントリを書き換えたら `scripts/sync_definition.py` で反映します（「定義を変えてみる」を参照）
- エントリは `orders` テーブルに紐づけていません。紐づけた用語は「定義なし」の条件にも届く恐れがあるためです
- どちらの条件でも、Data Agent への依頼は 1 問につき 1 回です。回答はその分析結果から書き、根拠の検査も同じ結果に対して行います

## 構成

```text
client -> Cloud Run: agent (private, ID token)
            |-- Catalog                       業務定義（確定売上）。起動時に読む
            |-- Gemini (Vertex AI)            Root Agent のモデル
            |-- Data Agent (raw / defined)    分析。SQL を書いて実行する
            |     `-- BigQuery                orders テーブル
            `-- Cloud Trace                   model / tool / Data Agent 呼び出し単位
```

- `sales/root.py`: Root Agent。条件ごとに 1 つ作る
- `sales/catalog.py`: Catalog のエントリの作成と読み取り
- `sales/data_agent.py`: Data Agent の定義、作成、質問
- `sales/data.py`: データと BigQuery への投入 SQL
- `agents/sales_*/agent.py`: ADK の app
- `server.py`: ADK API server。`/` で比較画面も返す
- `web/index.html`: 比較画面（1 ファイル、build なし）
- `scripts/`: 準備（`provision.py`）、deploy、定義の反映（`sync_definition.py`）、実行と trace、手元からの転送（`proxy.py`）、片付け（`cleanup.py`）
- `semantics/`: 共通の指示と、Catalog のエントリを最初に作るときの本文

## 準備

Python 3.11 以上、gcloud、[Agents CLI](https://google.github.io/agents-cli/) が必要です。課金が有効な Google Cloud project を 1 つ用意し、その owner で実行してください。

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
python scripts/provision.py     # API 有効化、dataset、orders テーブル、Catalog のエントリ、Data Agent 2 つ
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

`sales_defined` の trace には、使った定義が `sales.definition_entry`、`sales.definition_update_time`、`sales.definition_sha256` として、問い合わせた Data Agent が `sales.data_agent` として残ります。

## 定義を変えてみる

Google Cloud コンソールの Catalog で、エントリ「確定売上」（entry group `sales-demo`）の概要を書き換えます。たとえば「検収済」を「受注と検収済」に変えます。そのあと、次を実行します。

```bash
python scripts/sync_definition.py
```

いまの定義の版に対応する Data Agent を作り（すでにあれば何もしません）、Cloud Run の新しい revision を起動して Root Agent に読ませます。表示される sha256 が、次の実行の trace に残る値と同じになります。

- 動いている Root Agent は、起動時に読んだ版と、その版の Data Agent を使い続けます。新しい revision に替わるまで、回答も trace に残る版も前のままです。revision の起動に失敗しても、この組み合わせは崩れません
- Root Agent は起動時に Catalog を読み、その版の Data Agent があることを確かめます。無ければ起動しません
- このため、Catalog を書き換えたあと `sync_definition.py` を実行するまでの間に Cloud Run が新しい instance を起動すると、その instance は起動に失敗し、質問に答えられなくなります（古い定義では答えません）。書き換えたら続けて `sync_definition.py` を実行してください
- 以前の版の Data Agent は残ります。定義を元に戻すと、その版の Data Agent をそのまま使います

## 片付け

```bash
python scripts/cleanup.py          # 削除するものを表示
python scripts/cleanup.py --yes    # 削除
gcloud run services delete sales-analytics-agent --project "$PROJECT_ID" --region asia-northeast1
```

`cleanup.py` が削除するのは、`provision.py` と `sync_definition.py` が作った `orders` テーブル、Data Agent（定義の版ごとに 1 つ増えます）、Catalog のエントリだけです。BigQuery の dataset（`sales_demo`）は残るので、ほかに使っていなければ削除してください。

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

Google Cloud には接続しません。手計算の合計、条件ごとの定義の有無、定義が Catalog から渡ること、定義の版ごとに Data Agent が分かれること、既存の Data Agent を指示文と参照先が同じときだけ使い回すこと、Data Agent への依頼が 1 問につき 1 回であることを確かめます。

## 確認状況（2026-11-21）

実 project で、2 条件を交互に 5 回ずつ実行した結果です。回答は実行のたびに変わることがあります。この計測は、定義の版ごとに Data Agent を分ける変更の 1 つ前の commit（`c59831e`）で行いました。変更後は、定義の書き換え・反映・切り戻しで回答と版が入れ替わることだけを確かめています。

| 条件 | 回答（5 回） | 全体 | うち Data Agent |
|---|---|---|---|
| `sales_raw` | 検収済だけを合計し −12.0% が 3 回、取消以外を合計し +7.4% が 2 回 | 28.4〜37.2 秒が 4 回、59.2 秒が 1 回 | 11.1〜17.0 秒 |
| `sales_defined` | 検収済だけを合計し −12.0% が 5 回 | 21.7 秒が 1 回、58.1〜94.8 秒が 4 回 | 9.7〜11.5 秒 |

- `sales_defined` の SQL は 5 回とも `WHERE status = '検収済'` でした。Trace に残った定義の版（`sales.definition_sha256`）は 5 回とも `4e3bb8903c6d` です
- 全体が 58 秒を超えた 5 回は、Root Agent の最初の model 呼び出しが 29.7〜76.3 秒でした（Cloud Trace で確認）。最も長い 94.8 秒の回でも、Data Agent は 11.2 秒です。この待ちは速い時間帯を選ばずに測った結果で、条件の違いによるものとは確認していません

データ構成の出発点は [Data & AI Summit '26 Fall ハンズオン](https://github.com/mbk-digital/data-ai-summit-2026-fall-handson)（Apache License 2.0）です。
