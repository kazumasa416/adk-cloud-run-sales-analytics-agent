# EC Analytics Agent on Cloud Run

架空の EC 企業のデータに、業務の質問を 1 つ投げるデモです。ADK の Root Agent が Conversational Analytics の Data Agent に分析を頼み、BigQuery で集計して答えます。Cloud Run の非公開 service として動かします。

同じ質問を、条件を 1 つずつ変えて 3 回実行できます。

| app 名 | 業務定義 | 分析結果の取得 | 前の条件から変えたこと |
|---|---|---|---|
| `ec_raw` | なし | 利用箇所ごとに取得 | — |
| `ec_defined` | あり | 利用箇所ごとに取得 | 業務定義だけ |
| `ec_reuse` | あり | 同じ request 内で共有 | 取得の仕方だけ |

- 3 条件とも同じ 5 テーブル（28 行）を参照します。業務定義を組み込んだ view は比較に使いません
- 業務定義の有無は、Data Agent と Root Agent の両方でそろえています
- 「利用箇所」は 2 つあります。回答を書く処理と、根拠を検査する処理です。それぞれが取得すると、1 問につき Data Agent を 2 回呼びます

データ、業務定義、質問は [Data & AI Summit '26 Fall ハンズオン](https://github.com/mbk-digital/data-ai-summit-2026-fall-handson)（Apache License 2.0）から取り、Cloud Run 向けに組み直しました。

## 構成

```text
client -> Cloud Run: agent (private, ID token)
            |-- Gemini (Vertex AI)            Root Agent のモデル
            |-- Data Agent (raw / defined)    分析。SQL を書いて実行する
            |     `-- BigQuery                5 テーブル
            `-- Cloud Trace                   node / model / Data Agent 呼び出し単位
```

- `ec/root.py`: Root Agent。条件ごとに 1 つ作る
- `ec/data_agent.py`: Data Agent の定義、作成、質問
- `ec/data.py`: データと BigQuery への投入 SQL
- `agents/ec_*/agent.py`: ADK の app。app 名が条件を選ぶ
- `server.py`: ADK API server
- `semantics/`: 共通の指示と、会社の業務定義

## 準備

Python 3.11 以上、gcloud、[Agents CLI](https://google.github.io/agents-cli/) が必要です。

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
python scripts/provision.py     # API 有効化、dataset、5 テーブル、Data Agent 2 つ
./scripts/deploy.sh             # agent service を Cloud Run に deploy
```

`agents-cli` は gcloud の login より Application Default Credentials を優先します。
`gcloud auth application-default login` で deploy 先と同じ account にそろえてください。

## 実行

```bash
export SERVICE_URL="$(gcloud run services describe ec-analytics-agent --region asia-northeast1 --format='value(status.url)')"
python scripts/ask.py ec_raw ec_defined ec_reuse   # 同じ質問を 3 条件で
python scripts/trace.py 3                          # 直近 3 回の Cloud Trace
```

既定の質問は「2026年7月と比べて8月の粗利益が最も減ったカテゴリと、8月から7月を引いた差額を教えてください。」です。
会社の定義での答えは Electronics、−64,000 円です（`ec/data.py` の 28 行から手計算できます）。

## 費用と注意

- Conversational Analytics の Data Agent は 2026 年 12 月 31 日まで無料試用期間です。それ以降の料金は公式ページを確認してください
- Gemini、BigQuery の query、Cloud Run、Cloud Trace、Cloud Build には別途料金がかかります
- session は instance の memory に置いています。`SESSION_SERVICE_URI` を指定しない限り、instance が入れ替わると消えます
- 使い終わったら Cloud Run service、BigQuery dataset、Data Agent を削除してください

## テスト

```bash
pip install -r requirements.txt -c constraints.txt pytest
pytest -q
```

Google Cloud には接続しません。条件ごとの定義の有無と、Data Agent の呼び出し回数を確かめます。

## 確認状況（2026-11-21）

- 確認済み: Cloud Run の service account から Data Agent を呼び、3 条件で回答が返ること。Cloud Trace に Data Agent の呼び出しと model の token 数が出ること
- 未確認: 画面（比較 UI）はまだありません。`scripts/ask.py` で呼び出します
