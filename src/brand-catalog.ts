import type { Item } from './types';
import { config } from './config';
import { t } from './i18n';

export type OfficialBrand = { src: string; label: string; official: boolean };

/** Official logos shipped in public/art/brands (provenance in sources.json). */
export const brands: Record<string, OfficialBrand> = {
  'openai': { src: '/art/brands/openai.png', label: 'OpenAI', official: true },
  'claude': { src: '/art/brands/claude.ico', label: 'Claude', official: true },
  'gemini': { src: '/art/brands/gemini.svg', label: 'Gemini', official: true },
  'google-workspace': { src: '/art/brands/google-workspace.ico', label: 'Google Workspace', official: true },
  'gmail': { src: '/art/brands/gmail.ico', label: 'Gmail', official: true },
  'google-calendar': { src: '/art/brands/google-calendar.svg', label: 'Google Calendar', official: true },
  'google-drive': { src: '/art/brands/google-drive.ico', label: 'Google Drive', official: true },
  'goose': { src: '/art/brands/goose.png', label: 'Goose', official: true },
  'cmux': { src: '/art/brands/cmux.png', label: 'cmux', official: true },
  'xai': { src: '/art/brands/xai.png', label: 'xAI', official: true },
  'github': { src: '/art/brands/github.png', label: 'GitHub', official: true },
  'vercel': { src: '/art/brands/vercel.png', label: 'Vercel', official: true },
  'cloudflare': { src: '/art/brands/cloudflare.jpg', label: 'Cloudflare', official: true },
  'supabase': { src: '/art/brands/supabase.png', label: 'Supabase', official: true },
  'tailscale': { src: '/art/brands/tailscale.png', label: 'Tailscale', official: true },
  'salesforce': { src: '/art/brands/salesforce.png', label: 'Salesforce', official: true },
  'ffmpeg': { src: '/art/brands/ffmpeg.png', label: 'FFmpeg', official: true },
  'freee': { src: '/art/brands/freee.png', label: 'freee', official: true },
  'adobe': { src: '/art/brands/adobe.png', label: 'Adobe', official: true },
  'canva': { src: '/art/brands/canva.png', label: 'Canva', official: true },
  'fal': { src: '/art/brands/fal.png', label: 'fal.ai', official: true },
  'figma': { src: '/art/brands/figma.png', label: 'Figma', official: true },
  'notion': { src: '/art/brands/notion.png', label: 'Notion', official: true },
  'slack': { src: '/art/brands/slack.png', label: 'Slack', official: true },
  'zoom': { src: '/art/brands/zoom.png', label: 'Zoom', official: true },
  'lovable': { src: '/art/brands/lovable.ico', label: 'Lovable', official: true },
  'beeper': { src: '/art/brands/beeper.png', label: 'Beeper', official: true },
  'playwright': { src: '/art/brands/playwright.svg', label: 'Playwright', official: true },
};

// Substring → brand. Order matters: the first match wins. Extend or override via `brands` in guild.config.json.
const heuristics: [RegExp, string][] = [
  [/google[-_ ]?workspace|\bgws\b/, 'google-workspace'], [/gmail/, 'gmail'], [/google[-_ ]?calendar|gcal/, 'google-calendar'], [/google[-_ ]?drive|gdrive/, 'google-drive'],
  [/gemini|notebooklm/, 'gemini'], [/claude|anthropic/, 'claude'], [/gpt|codex|openai|whisper/, 'openai'], [/grok|xai/, 'xai'],
  [/github|\bgh\b/, 'github'], [/vercel/, 'vercel'], [/cloudflare|wrangler/, 'cloudflare'], [/supabase/, 'supabase'], [/tailscale/, 'tailscale'],
  [/salesforce|\bsf\b/, 'salesforce'], [/ffmpeg|ffprobe/, 'ffmpeg'], [/freee/, 'freee'], [/adobe/, 'adobe'], [/canva/, 'canva'], [/\bfal\b/, 'fal'],
  [/figma/, 'figma'], [/notion/, 'notion'], [/slack/, 'slack'], [/zoom/, 'zoom'], [/lovable/, 'lovable'], [/beeper/, 'beeper'], [/playwright/, 'playwright'],
  [/goose/, 'goose'], [/cmux/, 'cmux'],
];

export function officialBrand(item: Item): OfficialBrand | null {
  if (!['model', 'cli', 'connection'].includes(item.category)) return null;
  const neutral: OfficialBrand = { src: '/art/brands/neutral.svg', label: t('art.localTool'), official: false };
  const overrides = config.brands || {};
  const haystack = `${item.id} ${item.name}`.toLowerCase();
  for (const [needle, key] of Object.entries(overrides)) {
    if (haystack.includes(needle.toLowerCase()) && brands[key]) return brands[key];
  }
  const hit = heuristics.find(([pattern]) => pattern.test(haystack));
  return hit ? brands[hit[1]] : neutral;
}
