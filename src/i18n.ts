import en from './locales/en.json';
import ja from './locales/ja.json';
import { config } from './config';

type Dict = Record<string, string | string[]>;
const locales: Record<string, Dict> = { en: en as Dict, ja: ja as Dict };

export const language = locales[config.language] ? config.language : 'en';
const dict = locales[language];
const fallback = locales.en;

/** Translate a key with `{name}` placeholders. Missing keys fall back to English, then to the key itself. */
export function t(key: string, vars: Record<string, string | number> = {}): string {
  const raw = dict[key] ?? fallback[key] ?? key;
  const text = Array.isArray(raw) ? raw.join(', ') : raw;
  return text.replace(/\{(\w+)\}/g, (_, name: string) => (name in vars ? String(vars[name]) : `{${name}}`));
}

/** Translate a key whose value is a list. */
export function tl(key: string): string[] {
  const raw = dict[key] ?? fallback[key];
  return Array.isArray(raw) ? raw : typeof raw === 'string' ? [raw] : [];
}

export const intlLocale = language === 'ja' ? 'ja-JP' : 'en-US';
