# AI Guild

**自分のAI環境を、小さなゲームの世界として眺める。** エージェントは「仲間」、スキルやMCPは「装備」、定期ジョブは「クエスト」、トークン消費は「レベル」。自分でホストするプライベートなサイトで、見た目もキャラクターもあなたが決めます。初期セットアップは Claude Code や Codex などのコーディングエージェントが**インタビュー形式**で進めます。どんなテイストにするか、誰を住まわせるかを聞いたうえで、あなたのマシンをスキャンして組み立てます。

English: [README.md](README.md)

## 何が見えるか

| カテゴリ | 取得元（名前とメタデータだけ） |
|---|---|
| モデル | Codexのモデルキャッシュ、Claude Codeの設定、Gemini / Goose / Grok の設定 |
| CLI | 設定したリストを `which` と `--version` で確認（claude, codex, gemini, gh, wrangler, ffmpeg …） |
| 連携 | Codex / Claude Code（グローバル＋プロジェクト）/ `.mcp.json` の MCPサーバー**名**、有効化プラグイン |
| スキル | 共有・Codex・Claude のスキルルートとプラグインキャッシュ内の `SKILL.md` |
| エージェント | `~/.claude/agents/*.md`、プラグインのエージェント、`~/.codex/agents/*.toml`。肩書き・得意技・依頼例はセットアップ時に書きます |
| ハーネス | Claude のフック（イベント名）、`~/.claude/rules/*.md`、`CLAUDE.md` / `AGENTS.md` の有無 |
| 自動実行 | launchd（macOS）、crontab、systemd ユーザータイマー。スケジュール・読込/実行状態・直近終了コード |
| 利用量 | Codex と Claude Code のセッションログ → 日別トークンと累計レベル |

すべて**読み取り専用**・**メタデータのみ**です。何を集めて何を集めないかは [docs/PRIVACY.md](docs/PRIVACY.md) に明記しています。サーバーは許可リスト外の値や秘密情報らしき文字列を拒否します。

## はじめかた

```sh
git clone https://github.com/elefant-coder/ai-guild.git
cd ai-guild
npm install
npm run dev          # 架空データのデモが http://127.0.0.1:4317 で開きます
```

続けて、このフォルダを **Claude Code** か **Codex** で開いて、こう言ってください。

> AI Guild をセットアップして

エージェントは [SETUP.md](SETUP.md) の手順で、言語、ギルド名、ビジュアルのプリセット（`lavender-toybox` / `midnight-arcade` / `paper-atelier` / `pixel-dungeon` / 自由指定）、仲間のコンセプト（動物・ロボット・ちびキャラ冒険者・ぬいぐるみ・自分のペット…）、スキャンするマシン、公開するかどうか、匿名化したいジョブ名などを順に聞きます。そのうえで `guild.config.json` を書き、マシンをスキャンし、画像生成ツールがあればキャラ絵を作り、プレビューを一緒に確認してから、合言葉付きで Cloudflare Workers に公開します。

手動でやりたい場合は [docs/CUSTOMIZING.md](docs/CUSTOMIZING.md) と [docs/DEPLOY.md](docs/DEPLOY.md) に同じ手順があります。

## 必要なもの

- Node 20 以上、Python 3.11 以上（標準ライブラリのみ。画像のリサイズには Pillow があると便利）
- コレクターは macOS か Linux（launchd / cron / systemd を読みます）。サイト自体はどこでも動きます
- 公開するなら無料の Cloudflare アカウント。ローカルだけでも使えます

## 仕組み

```
あなたのマシン                              Cloudflare
┌───────────────────────────┐             ┌──────────────────────────────┐
│ collector/scan.py  ──┐    │  HTTPS +    │ Worker: 合言葉ログイン、      │
│ collector/usage.py ──┼─► sync.py ──────►│ 署名Cookie、厳格な検証        │
│ collector/art.py   ──┘    │  bearer     │ Durable Object (SQLite):     │
│ guild.config.json          │             │ 台帳 · 利用量 · キャラ画像     │
└───────────────────────────┘             └──────────────┬───────────────┘
                                                          │ private JSON
                                                   React UI（スマホ優先）
```

- **コレクター**（Python）: スキャン→検証→bearer秘密でアップロード。常駐させると設定変更を検知して再スキャンし、30秒ごとに生存報告します。
- **Worker**: 所有者ひとり、合言葉ひとつ、署名付き HttpOnly Cookie ひとつ。ブラウザのセッションは書き込めず、コレクターは読み出せません。
- **UI**: React + Vite。英語と日本語を同梱。テーマ色は設定から適用。自分の絵ができるまでは決定的に決まるロボットのプレースホルダーを表示します。

詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 自分好みにする

- `guild.config.json` — 名前、プレイヤー、言語、マシン、テーマ色、画風、ホーム画面に出す仲間、エージェントの肩書き、ロゴの対応、コレクターの走査先とプライバシーパターン。スキーマは `docs/guild.config.schema.json`。
- `presets/` — テーマ＋画風のセット。
- `src/locales/` — `en.json` をコピーして別言語を追加。
- `collector/art.py plan` — 選んだ画風で仲間ごとのプロンプトを生成。好きな画像生成ツールに渡して `import` で取り込みます。

## コマンド

| | |
|---|---|
| `npm run dev` | ローカルプレビュー（`data/inventory.json` があれば実データ、なければデモ） |
| `npm run scan` · `npm run usage` | `data/inventory.json` / `data/stats.json` を書き出し |
| `npm run validate` | スキャン結果をスキーマと秘密パターンで検査 |
| `npm test` · `npm run test:collector` | Worker テスト · コレクターテスト |
| `npm run deploy` · `npm run secrets` | ビルド＋公開 · 合言葉と秘密の設定 |
| `python3 collector/sync.py --run` | 常時同期（または `node scripts/install-scheduler.mjs`） |
| `python3 collector/art.py plan|import|push|status` | キャラ画像のパイプライン |

## ライセンス

MIT。`public/art/brands/` のロゴは各社の商標で、識別目的にのみ使用しています。
