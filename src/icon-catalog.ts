import type { Item } from './types';

export type ToolPurpose = 'video' | 'design' | 'data' | 'security' | 'memory' | 'research' | 'writing' | 'automation' | 'harness';

/** Shared equipment artwork represents a purpose. Agents use characters instead (see characters.tsx). */
export function purposeOf(item: Item): ToolPurpose {
  const text = [item.name, item.summary, ...item.tags].join(' ').toLowerCase();
  const rules: [RegExp, ToolPurpose][] = [
    [/動画|video|remotion|hyperframes|切り抜き|kinetic|motion|film|animation/, 'video'],
    [/デザイン|design|画像|image|canva|figma|creative|写真|photo|illustration/, 'design'],
    [/会計|財務|finance|予算|budget|支払|請求|データ|data|分析|analytics|sheet|excel|csv|利用量|token|sql|database/, 'data'],
    [/security|認証|権限|安全|セキュリティ|password|auth|audit|secret/, 'security'],
    [/記憶|memory|context|知識|ナレッジ|knowledge|note/, 'memory'],
    [/調査|research|検索|search|notebooklm|review|検証|監査|テスト|test|品質|verify|qa/, 'research'],
    [/投稿|post|slack|メール|email|sns|送信|共有|share|配信|文書|writing|資料|doc|blog/, 'writing'],
    [/同期|sync|deploy|インフラ|cloudflare|vercel|docker|起動|cron|launchd|schedule/, 'automation'],
    [/設計|architecture|routing|route|ハーネス|harness|分担|code|開発|実装|react|next|cli|hook|rule/, 'harness'],
  ];
  return rules.find(([pattern]) => pattern.test(text))?.[1]
    || (item.category === 'harness' ? 'harness' : item.category === 'automation' ? 'automation' : 'writing');
}

export const purposeArtwork = (item: Item) => `/art/tools/${purposeOf(item)}.webp`;
