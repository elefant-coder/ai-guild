import resolved from '../generated/guild.config.js';
import type { GuildConfig, MachineConfig } from './types';

// generated/guild.config.json = guild.config.example.json deep-merged with your guild.config.json.
// It is produced by `npm run prepare-config`, which every npm script runs first.
export const config: GuildConfig = resolved as unknown as GuildConfig;

export const machines: MachineConfig[] = Array.isArray(config.machines) && config.machines.length
  ? config.machines : [{ id: 'main', label: 'This machine', kind: 'local' }];

export const machineLabel = (id: string) => machines.find(m => m.id === id)?.label || id;

/** Push theme tokens from the config onto :root so the CSS can stay static. */
export function applyTheme() {
  const root = document.documentElement.style;
  const c = config.theme?.colors || {};
  const map: Record<string, string | undefined> = {
    '--ink': c.ink, '--muted': c.muted, '--paper': c.paper, '--lav': c.surface, '--lav2': c.surface2, '--line': c.line,
    '--purple': c.accent, '--purple-dark': c.accentDark, '--coral': c.warm, '--yellow': c.gold, '--game-gold': c.gold,
    '--game-purple': c.accent, '--game-edge': c.edge, '--game-shadow': c.shadow, '--dark-panel': config.theme?.darkPanel,
    '--font': config.theme?.font,
  };
  for (const [name, value] of Object.entries(map)) if (value) root.setProperty(name, value);
  document.documentElement.lang = config.language || 'en';
  document.title = config.guild?.name || 'AI Guild';
}
