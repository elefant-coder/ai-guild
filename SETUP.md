# SETUP.md — the guided setup (for Claude Code, Codex, or any coding agent)

This file is a **protocol for the AI coding agent** that sets up AI Guild for its user.
If you are the agent: follow it top to bottom. Speak the user's language. Ask, don't assume, on every taste decision.
If you are a human reading this: open the repo in Claude Code or Codex and say “set up AI Guild” (or 「AI Guildをセットアップして」).

The result is a private, passphrase-protected site that shows the user's own AI environment as a small game world:
agents as companions, skills and MCP connections as gear, scheduled jobs as quests, token usage as a level.
The user decides how it looks and who lives in it. You build it.

---

## 0. Ground rules for the agent

- **Taste is the user's.** Never pick a colour palette, a mascot species or a character style silently. Offer choices, then apply their answer.
- **Privacy first.** The collector reads names of config entries, never their values. Never read `~/.ssh`, `*.env`, `auth.json`, `credentials*`, token files, or paste any secret into the chat or into any file in this repo. `guild.config.json`, `data/` and generated art are git-ignored on purpose; do not force-add them.
- **No surprise spending or sending.** Ask before running anything that costs money (image APIs, paid tiers) or sends data anywhere (deploying, uploading art, installing a background job).
- **One question block at a time.** Group the interview into the short blocks below; do not dump 15 questions at once. If the user says “just pick for me”, use the defaults marked ★ and keep going.
- **Verify before you report.** Run the commands, read the output, open the preview. “Build passed” is not “it works”.

## 1. Preconditions (check silently, report only problems)

```sh
node -v        # >= 20
python3 --version   # >= 3.11
git --version
npm install
npm test               # Worker tests (uses example config)
python3 -m unittest discover -s collector/tests -t .   # collector tests
```

Optional, needed only for certain choices: `wrangler` (Cloudflare deploy: `npx wrangler login`), Pillow (`pip install pillow`, for resizing art), an image-generation tool available to you (see §5).

## 2. Interview — block A: identity and language

Ask, in one message:

1. **Language** for the interface: Japanese, English, or another (currently shipped: `ja`, `en`; another language means you add `src/locales/<code>.json` by copying `en.json` and translating it).
2. **Guild name** shown in the sidebar and on the login page (★ “AI Guild”). Suggest something personal: “Mika's Workshop”, “Night Owl Guild”, 「ゆうきの秘密基地」.
3. **Player name** shown in the HUD (★ “Player”). Warn once: the site is private, but a nickname is safer than a full legal name.
4. **Time zone** (★ the machine's zone: run `date +%Z` / `python3 -c "import datetime;print(datetime.datetime.now().astimezone().tzinfo)"`, then confirm the IANA name such as `Asia/Tokyo`).

## 3. Interview — block B: look and characters

This is the block the user cares about most. Present the presets from `presets/` **with one line each**, then ask which one, or invite them to describe their own. Ask each sub-question explicitly:

1. **Visual preset** (each preset fixes a palette, font, art style):
   - ★ `lavender-toybox` — white and lavender, rounded, glossy 3D toys. Calm and friendly.
   - `midnight-arcade` — deep navy, neon accents, arcade cabinet energy.
   - `paper-atelier` — warm paper, ink, terracotta. Sketchbook feel.
   - `pixel-dungeon` — retro 16-bit dungeon, chunky pixels, gold on stone.
   - **Custom** — ask for 2–3 adjectives and a favourite colour, then write the palette yourself.
2. **What are the companions?** (the agent characters)
   - ★ Animals, one species per agent, each holding one tool of their trade
   - Robots / droids
   - Chibi adventurers (fantasy classes: mage, ranger, smith…)
   - Plush mascots
   - Abstract spirits / elemental shapes
   - The user's own idea (their pet, a favourite genre, an existing mascot)
   Also ask for **must-haves and must-avoids** (e.g. “no humans”, “always a cat somewhere”, “nothing scary”).
3. **What are the tools?** (skills, harness rules, jobs) ★ Polished single objects (camera, key, notebook…) / flat icons / keep the built-in tool images.
4. **Player avatar**: generate one in the same style (describe it: ★ “a friendly guide character holding a map”), use an image the user provides (they drop it at `public/art/player.webp`), or keep the built-in default.
5. **Which companions go on the home screen** (“Choose your partner” carousel): ★ the first six found, or a list of agent names.

Write the answers down (you will need them in §4 and §5). Confirm the block back in two or three lines before moving on.

## 4. Interview — block C: machines, hosting, privacy

1. **Machines to scan**: ★ only this machine; or also remote machines reachable with `ssh <alias>` (the same collector script is streamed over SSH; only metadata comes back). Collect `id` (lowercase slug), `label`, and ssh alias for each.
2. **Hosting**: ★ Cloudflare Workers (free tier is enough; private behind a passphrase; needs `npx wrangler login`), or **local only** for now (`npm run dev` on this machine; can deploy later).
3. **Privacy review**: tell the user exactly what is collected (see `docs/PRIVACY.md`, 6 bullet points) and ask for **label patterns to anonymise**, e.g. client names in job labels (`*acme*`, `com.example.*`). These become `collector.jobs.privatePatterns`; matching jobs appear as “Private job · a1b2” with no label.
4. **Continuous sync**: ★ yes, install a background job (launchd on macOS, systemd user unit on Linux) that re-scans on change and every minute; or **manual** (`npm run scan` when they feel like it).

## 5. Build it

### 5.1 Write `guild.config.json`

Copy `guild.config.example.json` to `guild.config.json` and edit **only the keys you have answers for** (unspecified keys inherit the example). Then, if a preset was chosen, merge `presets/<name>.json` into the `theme` and `art` sections. Run:

```sh
npm run prepare-config     # validates and generates generated/guild.config.*
```

### 5.2 Scan the machine and review the result together

```sh
npm run scan                       # writes data/inventory.json
python3 collector/validate.py data/inventory.json
```

Show the user the per-category counts and the **list of agent names** found. Then read each agent's definition file you find under `~/.claude/agents/`, `~/.codex/agents/` (and plugin caches) and write, in the chosen language, a `roles` entry per agent in `guild.config.json`:

```json
"roles": {
  "quill": { "title": "Frontend wizard", "strengths": ["React and TypeScript", "Accessible UI", "Animation"], "exampleTask": "Build the settings page from the design spec" }
}
```

Re-run `npm run scan` so the titles appear. Grep `data/inventory.json` for anything that looks private (client names, tokens, `/Users/`, `/home/`); if you find something, fix it with `privatePatterns` or `excludePatterns` and rescan. Ask the user to skim the agent and job names once.

### 5.3 Character art

```sh
python3 collector/art.py plan            # writes data/art-plan.json: one prompt per agent (and tools with --include-tools)
```

Each entry has a ready-to-use prompt built from the user's answers in §3. Now generate the images. In order of preference:

1. **You have an image tool** (Codex built-in image generation, an MCP image server, a skill): generate each prompt as a square PNG, save under `data/art-incoming/<id>.png`, then `python3 collector/art.py import --id <id> --file data/art-incoming/<id>.png`. Do the featured companions first, show 2–3 to the user, and **ask whether the style is right before generating the rest**.
2. **Codex CLI with a ChatGPT subscription is installed** and the user agrees: `python3 collector/art.py generate` (rate-limited, no API keys).
3. **Neither**: keep the built-in robot placeholders (they are deterministic per agent and look fine) and leave `data/art-plan.json` so the user can paste prompts into any generator and drop results into `data/art-incoming/`. Explain this in one sentence.

Never use a paid image API without the user saying yes to the cost first.

### 5.4 Preview and iterate on taste

```sh
npm run dev      # http://127.0.0.1:4317
```

Open it (a browser tool if you have one, otherwise ask the user to open it), look at Home, Companions, Gear, Automation. Ask: “Does this feel like yours? What should change?” Adjust `theme.colors` in `guild.config.json`, or the CSS in `src/*.css` for deeper changes, until they are happy. Mobile width matters: check 390px.

### 5.5 Deploy (if Cloudflare was chosen)

```sh
npx wrangler login               # the user does this; it opens a browser
npm run deploy                   # builds and deploys; prints https://ai-guild.<account>.workers.dev
npm run secrets                  # interactive: passphrase + generated session/ingest secrets, writes ~/.config/ai-guild/
python3 collector/sync.py --check && python3 collector/sync.py --once
python3 collector/art.py push    # uploads local art to the site
```

Verify like a stranger: open the URL in a private window → login page appears; `curl -s -o /dev/null -w "%{http_code}" https://<url>/api/inventory` → `401`. Then log in with the passphrase and confirm the data and art show. Tell the user the URL and that the passphrase is the only key (there is no reset; they can rotate it with `npm run secrets`).

### 5.6 Background sync (if chosen)

```sh
node scripts/install-scheduler.mjs           # launchd (macOS) or systemd --user (Linux); --uninstall to remove
```

Confirm with `launchctl list | grep ai-guild` or `systemctl --user status ai-guild-sync`, then reload the site and check “Sync & data” shows a fresh heartbeat.

## 6. Hand-off

Finish with a short message that stands on its own: the URL (or `npm run dev`), the passphrase reminder, how to rescan, how to regenerate art for a new agent (`art.py plan` → generate → `import` → `push`), and where taste lives (`guild.config.json`, `presets/`, `src/*.css`). Offer, in one line, to change anything they did not like.

Do not commit `guild.config.json`, `data/`, or generated character art unless the user explicitly wants their configuration published.
