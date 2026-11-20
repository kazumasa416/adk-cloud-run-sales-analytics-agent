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

## 3 つの条件

同じ質問を、条件を 1 つずつ変えて実行できます。app 名が条件を選びます。

| app 名 | 業務定義 | 分析結果の取得 | 前の条件から変えたこと |
|---|---|---|---|
| `sales_raw` | なし | 利用箇所ごとに取得 | — |
| `sales_defined` | あり | 利用箇所ごとに取得 | 業務定義だけ |
| `sales_reuse` | あり | 同じ request 内で共有 | 取得の仕方だけ |

- 3 条件とも同じ `orders` テーブルを参照します
- 業務定義（`semantics/business-rules.txt`、4 行）の有無は、Data Agent と Root Agent の両方でそろえています
- 「利用箇所」は 2 つあります。回答を書く処理と、根拠を検査する処理です。それぞれが取得すると、1 問につき Data Agent を 2 回呼びます

## 構成

```text
client -> Cloud Run: agent (private, ID token)
            |-- Gemini (Vertex AI)            Root Agent のモデル
            |-- Data Agent (raw / defined)    分析。SQL を書いて実行する
            |     `-- BigQuery                orders テーブル
            `-- Cloud Trace                   model / tool / Data Agent 呼び出し単位
```

- `sales/root.py`: Root Agent。条件ごとに 1 つ作る
- `sales/data_agent.py`: Data Agent の定義、作成、質問
- `sales/data.py`: データと BigQuery への投入 SQL
- `agents/sales_*/agent.py`: ADK の app
- `server.py`: ADK API server
- `semantics/`: 共通の指示と、会社の業務定義

## 準備

Python 3.11 以上、gcloud、[Agents CLI](https://google.github.io/agents-cli/) が必要です。

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
python scripts/provision.py     # API 有効化、dataset、orders テーブル、Data Agent 2 つ
./scripts/deploy.sh             # agent service を Cloud Run に deploy
```

`agents-cli` は gcloud の login より Application Default Credentials を優先します。
`gcloud auth application-default login` で deploy 先と同じ account にそろえてください。

## 実行

```bash
export SERVICE_URL="$(gcloud run services describe sales-analytics-agent --region asia-northeast1 --format='value(status.url)')"
python scripts/ask.py sales_raw sales_defined sales_reuse   # 同じ質問を 3 条件で
python scripts/trace.py 3                                   # 直近 3 回の Cloud Trace
```

## 費用と注意

- Conversational Analytics の Data Agent は 2026 年 12 月 31 日まで無料試用期間です。それ以降の料金は公式ページを確認してください
- Gemini、BigQuery の query、Cloud Run、Cloud Trace、Cloud Build には別途料金がかかります
- session は instance の memory に置いています。`SESSION_SERVICE_URI` を指定しない限り、instance が入れ替わると消えます
- 使い終わったら Cloud Run service、BigQuery dataset、Data Agent を削除してください
- データと数値は説明用の架空のものです

## テスト

```bash
pip install -r requirements.txt -c constraints.txt pytest
pytest -q
```

Google Cloud には接続しません。手計算の合計、条件ごとの定義の有無、Data Agent の呼び出し回数を確かめます。

## 確認状況（2026-11-21）

実 project で 1 回ずつ実行した結果です。回答は実行のたびに変わることがあります。

| 条件 | 回答 | 全体 | Data Agent |
|---|---|---|---|
| `sales_raw` | 取消以外を合計し、+930,000 円（+7.4%） | 34.1 秒 | 2 回 |
| `sales_defined` | 検収済だけを合計し、−1,440,000 円（−12%） | 76.6 秒 | 2 回 |
| `sales_reuse` | 検収済だけを合計し、−1,440,000 円（−12%） | 18.2 秒 | 1 回 |

- `sales_defined` の 76.6 秒のうち 51.8 秒は、Root Agent の最後の model 呼び出しでした（Cloud Trace で確認）。Data Agent の 2 回は合わせて 22.7 秒です
- 画面（比較 UI）はまだありません。`scripts/ask.py` で呼び出します

データ構成の出発点は [Data & AI Summit '26 Fall ハンズオン](https://github.com/mbk-digital/data-ai-summit-2026-fall-handson)（Apache License 2.0）です。
