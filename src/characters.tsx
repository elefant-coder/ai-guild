import React, { createContext, useContext, useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import type { Item } from './types';
import './characters.css';
import { purposeArtwork } from './icon-catalog';
import { officialBrand } from './brand-catalog';
import { AgentPortrait } from './game-art';
import { t } from './i18n';

export interface Character {
  status: 'ready' | 'queued' | 'generating' | 'failed';
  imageUrl?: string;
  updatedAt?: string;
  promptVersion?: string;
  contentHash?: string;
}
type CharacterState = { characters: Record<string, Character>; connected: boolean };
const Characters = createContext<CharacterState>({ characters: {}, connected: false });

export function CharacterProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<CharacterState>({ characters: {}, connected: false });
  useEffect(() => {
    let stopped = false, busy = false;
    const controller = new AbortController();
    async function refresh() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const response = await fetch('/api/characters', { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]) });
        if (!response.ok) throw new Error('characters unavailable');
        const body = await response.json();
        if (body.schemaVersion !== 1 || !body.characters || Array.isArray(body.characters)) throw new Error('invalid manifest');
        const characters: Record<string, Character> = {};
        for (const [id, candidate] of Object.entries(body.characters).slice(0, 2500)) {
          const value = candidate as Character;
          if (!/^[A-Za-z0-9@-]+$/.test(id) || !['ready', 'queued', 'generating', 'failed'].includes(value.status)) continue;
          const expected = `/api/characters/${id}/image`;
          const localImage = value.imageUrl?.startsWith('/art/characters/');
          if (value.status === 'ready' && value.imageUrl !== expected && !localImage) continue;
          characters[id] = value;
        }
        if (!stopped) setState({ characters, connected: true });
      } catch {
        if (!stopped) setState(previous => ({ ...previous, connected: false }));
      } finally { busy = false; }
    }
    void refresh();
    const interval = setInterval(() => void refresh(), 10000);
    const resume = () => void refresh();
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => { stopped = true; controller.abort(); clearInterval(interval); document.removeEventListener('visibilitychange', resume); window.removeEventListener('online', resume); };
  }, []);
  return <Characters.Provider value={state}>{children}</Characters.Provider>;
}

export const useCharacters = () => useContext(Characters);
const hasArt = (character?: Character) => character?.status === 'ready' && !!character.imageUrl;

export function NewCharacters({ items, onOpen }: { items: Item[]; onOpen: (item: Item) => void }) {
  const { characters } = useCharacters();
  const latest = items.filter(item => hasArt(characters[item.id]))
    .sort((a, b) => (characters[b.id].updatedAt || '').localeCompare(characters[a.id].updatedAt || '')).slice(0, 4);
  if (!latest.length) return null;
  return <section className="character-arrivals" aria-label={t('arrivals.aria')}>
    <div className="arrival-heading"><h2>{t('arrivals.title')}</h2><span>{t('arrivals.sub')}</span></div>
    <div className="arrival-list">{latest.map(item => <button key={item.id} onClick={() => onOpen(item)}>
      <CharacterPortrait item={item} size={100}/><span className="arrival-copy"><small>{t(`category.${item.category}.short`)}</small><strong>{item.name.replaceAll('_', ' ')}</strong><span>{t('arrivals.more')}</span></span>
    </button>)}</div>
  </section>;
}

export function CharacterPortrait({ item, size = 96, className = '' }: { item: Item; size?: number; className?: string }) {
  const { characters } = useCharacters();
  const character = characters[item.id];
  const [failedUrl, setFailedUrl] = useState('');
  const brand = officialBrand(item);
  const individual = hasArt(character);
  const isAgent = item.category === 'agent';
  const url = brand?.src || (individual
    ? `${character!.imageUrl}?v=${encodeURIComponent(character!.updatedAt || character!.contentHash || '1')}` : isAgent ? '' : purposeArtwork(item));
  const name = item.name;
  const ready = !!url && failedUrl !== url;
  const pending = character?.status === 'queued' || character?.status === 'generating';
  const alt = brand ? (brand.official ? t('art.logoAlt', { label: brand.label }) : t('art.toolMarkAlt', { name })) : isAgent ? t('art.characterAlt', { name }) : t('art.gearAlt', { name });
  const title = brand ? (brand.official ? `${name} · ${brand.label}` : t('art.noLogo', { name })) : individual || isAgent ? name : t('art.shared', { name });
  return <span className={`character-portrait ${brand ? 'brand-artwork' : isAgent ? 'agent-artwork' : 'tool-artwork'} ${ready || (isAgent && !pending) ? 'is-ready' : 'is-pending'} ${className}`} style={{ width: size, height: size }}>
    {ready ? <img src={url} alt={alt} title={title} width={size} height={size} loading="lazy" decoding="async" onError={() => setFailedUrl(url)} />
      : isAgent && !pending ? <AgentPortrait seed={`${item.name} ${item.summary}`} size={size} className="portrait-fallback"/>
      : <span className="character-placeholder" role="img" aria-label={`${name}: ${pending ? t('art.pending') : t('art.checking')}`}>
        <Sparkles size={Math.max(18, Math.min(40, size / 4))} aria-hidden="true"/>
        {size >= 96 && <small>{pending ? t('art.pending') : t('art.checking')}</small>}
      </span>}
  </span>;
}
