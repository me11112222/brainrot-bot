# BRAINROT 図鑑 Discord BOT

実ゲームのキャラ（モンスター）一覧を Discord で図鑑として閲覧できる BOT。
名前・レア度・分類・画像を表示し、ステータス/効果/入手方法は後から追記できる。

## 構成

```
discord-bot/
├─ bot.py              ... BOT本体（スラッシュコマンド）
├─ build_data.py       ... 331MonsterType一覧.txt → characters.json 生成＆画像名寄せ
├─ characters.json     ... 生成データ（自動生成・編集不要）
├─ stats_override.json ... ステータス/効果/入手方法を手で追記するファイル
├─ requirements.txt
├─ .env.example        ... .env のひな形
└─ README.md
```

画像は親フォルダの `../BRAINROT/*.PNG` を参照（コピー不要）。

## セットアップ

### 1. ライブラリ導入
```powershell
cd C:\AI\projects\event-tool\discord-bot
python -m pip install -r requirements.txt
```

### 2. Discord BOT を作る
1. https://discord.com/developers/applications → **New Application**
2. 左メニュー **Bot** → **Reset Token** でトークンを取得（これが `DISCORD_TOKEN`）
3. **OAuth2 → URL Generator** で `bot` と `applications.commands` を選び、
   **Bot Permissions は最小限だけ**にチェック（下記セキュリティ参照）→ 生成URLから招待
4. テスト用サーバーのID（サーバー右クリック→IDをコピー。開発者モードON必要）を控える＝`GUILD_ID`

## 🔒 セキュリティ（重要）

このBOTは **メッセージ送信しかしない**（チャンネル削除・BAN・権限変更などのコードは一切無い）。
万一の乗っ取り被害を最小化するため、**BOTには管理者権限を絶対に付けないこと。**

**付与する権限はこれだけ：**
- View Channels / Read Messages
- Send Messages
- Embed Links
- Attach Files
- Use Application Commands

→ こうしておけば、たとえトークンが漏れても攻撃者にできるのは「メッセージ送信」だけ。
   **サーバー削除・チャンネル削除・BAN・権限奪取は不可能**になる。

その他：
- **トークンは絶対に公開しない**（`.env` のみ。Git/チャット/スクショに載せない）。漏れたら即 **Reset Token**。
- スパム対策として **1ユーザー8回/6秒** のレート制限を実装済み（`bot.py` の `RATE_MAX/RATE_WINDOW`）。
- `/panel` は **Manage Server 権限を持つ人だけ** 実行可。

### 3. .env を作成
`.env.example` をコピーして `.env` にし、トークンとサーバーIDを記入。

### 4. データ生成
```powershell
python build_data.py
```

### 5. 起動
```powershell
python bot.py
```
`ログイン: ...` と出れば成功。Discordで `/` を打つとコマンドが出る。

## コマンド

| コマンド | 説明 |
|---|---|
| `/zukan name:<キャラ名>` | 1体の詳細（画像つき）。名前は入力途中で候補表示 |
| `/list [rarity] [category]` | 一覧。レア度や分類で絞り込み・ページ送り |
| `/random [rarity]` | ランダムで1体 |
| `/count` | レア度別の体数 |

## データの正本（CSV）

図鑑データは **`FightTheBRAINROT INDEX - verify_sheet.csv`** が正本。
列: 名前 / レア度 / ティア / 攻撃力 / 生産力 / 価格 / 入手方法 / 確率 / 百鬼 など。
これを `python build_from_csv.py` で `characters.json` に変換している。

### データを直したいとき
1. CSV（Googleスプレッドシートからエクスポートしたもの）を編集
2. `python build_from_csv.py` を実行 → `characters.json` 再生成
3. BOT再起動

### 画像のしくみ
- 画像ソースは `C:\AI\projects\IndexPng`（UEFN書き出しの全スキン）。`_1Default` を優先採用。
- `build_from_csv.py` が、CSVの各キャラに画像を名寄せ → **必要分だけ `discord-bot/images/` にコピー**（BOT自己完結＝サーバー移行が楽）。
- Defaultスキンが無いキャラは他スキン（例 `_8Yokai`）で代替。名前が画像と違う場合もCSVの「現在の名前(OCR)」列で拾う。
- 画像を更新したいときは `IndexPng` に新PNGを置いて `python build_from_csv.py` を再実行。

> 未登録: `百鬼`（日本語名でファイル無し）/ `Meowl`（IndexPngに未export）。必要なら追加exportを。
> `build_data.py` / `stats_override.json` / `../BRAINROT/` は旧txtベースの初期版（現在は未使用）。
