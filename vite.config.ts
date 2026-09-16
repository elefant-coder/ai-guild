import { readFileSync } from 'node:fs';
import { rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import { localPreview } from './server/dev-preview.mjs';

/** Title, language and description come from the resolved guild config. */
function guildHtml(): Plugin {
  const config = JSON.parse(readFileSync(resolve(process.cwd(), 'generated/guild.config.json'), 'utf8'));
  const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] as string));
  return {
    name: 'guild-html',
    transformIndexHtml(html) {
      return html
        .replace('<html lang="en">', `<html lang="${escape(config.language || 'en')}">`)
        .replace('<title>AI Guild</title>', `<title>${escape(config.guild?.name || 'AI Guild')}</title>`)
        .replace('content="A private, game-style catalog of your AI environment."', `content="${escape(config.guild?.description || '')}"`)
        .replace('content="#f7f4ff"', `content="${escape(config.theme?.colors?.surface || '#f7f4ff')}"`);
    },
  };
}

export default defineConfig({
  build: { outDir: 'dist/client' },
  plugins: [localPreview(), guildHtml(), {
    name: 'guild-private-data',
    async closeBundle() {
      // Production reads inventory from the authenticated API, never from a bundled file.
      await rm(resolve(process.cwd(), 'dist/client/demo'), { recursive: true, force: true });
    },
  }],
});
