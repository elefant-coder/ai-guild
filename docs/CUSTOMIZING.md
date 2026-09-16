# Customizing

Everything the setup interview decides lives in `guild.config.json`. Start from the example:

```sh
cp guild.config.example.json guild.config.json
npm run prepare-config     # validates; regenerates generated/
```

Keys you do not set inherit the example values.

## Identity

```json
"language": "ja",
"guild": { "name": "ゆうきの秘密基地", "tagline": "仲間と道具を、ひとつの場所に。", "description": "..." },
"player": { "name": "Yuki", "avatar": "/art/player.webp" },
"timezone": "Asia/Tokyo"
```

`player.avatar` points at a file you place in `public/art/`. If it is missing, a built-in avatar is drawn.

## Theme

`theme.colors` maps onto the CSS variables used by every component:

| key | used for |
|---|---|
| `accent` / `accentDark` | buttons, active nav, badges, level ring |
| `ink` / `muted` | text |
| `paper` / `surface` / `surface2` / `line` | backgrounds, panels, borders |
| `warm` / `gold` | warnings, stars, medal |
| `edge` / `shadow` | the 3D panel edges |
| `darkPanel` | the “Automation quests” dark card |
| `font` | the CSS font stack |

Presets in `presets/` are full `theme` + `art` bundles; merge one into your config or write your own. For deeper changes edit `src/styles.css` (layout) and `src/game-ui.css` (the tactile 3D look). Navigation and tool icons live in `public/art/navigation` and `public/art/tools`; replace them with your own square WebP files of the same names.

## Companions

- `agents.featured`: agent names or ids to show in the home carousel (first six found if empty).
- `roles`: per-agent copy shown in the UI. The collector merges it into each agent item:

```json
"roles": {
  "quill": { "title": "Frontend wizard", "strengths": ["React and TypeScript", "Accessible UI"], "exampleTask": "Build the settings page from the design spec" }
}
```

## Art

`art.style`, `art.characterConcept`, `art.subjects` (one subject per agent, assigned deterministically), `art.toolStyle`, `art.constraints` feed `python3 collector/art.py plan`, which writes one prompt per item to `data/art-plan.json`. Generate images with any tool, then:

```sh
python3 collector/art.py import --id agent-quill --file ~/Downloads/quill.png
python3 collector/art.py push        # to the deployed site
```

Images are resized to 512 px and stored as WebP under `public/art/characters/` (git-ignored). `art.provider` can be `manual` (default) or `codex-cli` to let `art.py generate` call a locally installed Codex CLI with a ChatGPT subscription.

## Brands

Models, CLIs and connections show an official logo when the item id or name contains a known brand word. Override or extend with `"brands": { "my-internal-tool": "github" }` (substring → brand key from `src/brand-catalog.ts`).

## Collector

```json
"collector": {
  "roots": { "claude": "~/.claude", "codex": "~/.codex", "sharedSkills": "~/.agents/skills" },
  "projectRoots": ["~/Projects", "~/code"],
  "clis": ["claude", "codex", "gh", "wrangler"],
  "jobs": { "includePatterns": ["*"], "excludePatterns": ["com.apple.*"], "privatePatterns": ["*acme*"] },
  "usage": { "providers": ["codex", "claude"] }
},
"machines": [
  { "id": "main", "label": "Studio Mac", "kind": "local" },
  { "id": "laptop", "label": "Laptop", "kind": "ssh", "host": "laptop" }
]
```

Remote machines are scanned by streaming `collector/scan.py` over `ssh <host>`; only the resulting JSON comes back.

## Languages

Copy `src/locales/en.json` to `src/locales/<code>.json`, translate, register it in `src/i18n.ts`, and set `"language": "<code>"`. The login page has its own two-language table in `server/login-page.js`.
