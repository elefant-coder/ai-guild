import React from 'react';
import { t } from './i18n';

type PortraitProps = { seed: string; size?: number; className?: string; animated?: boolean };
const ink = '#11142a', ivory = '#fff6df', violet = '#9c7aff', coral = '#ff936e', yellow = '#f9d65c', blue = '#6eb9ff';
type Kind = 'sage' | 'coder' | 'guard' | 'dj' | 'pilot' | 'oracle';
const kinds: Kind[] = ['sage', 'coder', 'guard', 'dj', 'pilot', 'oracle'];

/** Pick a portrait archetype from an agent's name or summary. Deterministic, so the same agent always looks the same. */
export function portraitKind(seed: string): Kind {
  const s = seed.toLowerCase();
  if (/sage|research|audit|investig|deep|scholar|analy?st|librar/.test(s)) return 'sage';
  if (/code|dev|build|engineer|worker|forge|implement|front|back|full/.test(s)) return 'coder';
  if (/verif|review|guard|test|qa|security|sentinel|cipher|echo/.test(s)) return 'guard';
  if (/sound|voice|video|pixel|media|design|creative|muse|art/.test(s)) return 'dj';
  if (/route|navigat|plan|orchestr|manager|scout|pilot|lead/.test(s)) return 'pilot';
  if (/data|sheet|finance|ledger|metric|oracle|flux/.test(s)) return 'oracle';
  return kinds[[...seed].reduce((n, c) => n + c.charCodeAt(0), 0) % kinds.length];
}

/** A collectible cast of guild robots, used until custom character art exists. Readable from 64px. */
export function AgentPortrait({ seed, size = 96, className = '', animated = false }: PortraitProps) {
  const kind = portraitKind(seed), id = `portrait-${React.useId().replace(/:/g, '')}`, accent = kind === 'guard' ? blue : kind === 'dj' ? coral : kind === 'oracle' ? yellow : violet;
  const label = t(`portrait.${kind}`);
  return <svg className={`agent-portrait ${className}`} width={size} height={size} viewBox="0 0 96 96" role="img" aria-label={label} style={{ overflow: 'visible' }}><defs><filter id={`${id}-shadow`} x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="4" stdDeviation="1.8" floodColor={ink} floodOpacity=".34"/></filter></defs><g filter={`url(#${id}-shadow)`} className={animated ? 'agent-portrait__float' : undefined}>
    <path d="M19 76h58l-5 10H24z" fill={ink}/><path d="M24 74h48l-4 7H28z" fill={accent}/><path d="M29 55h38l7 22H22z" fill={ivory} stroke={ink} strokeWidth="4" strokeLinejoin="round"/><path d="M27 66h42v7H27z" fill={accent}/><rect x="28" y="28" width="40" height="37" rx="8" fill={ivory} stroke={ink} strokeWidth="4"/><path d="M34 42h28v14H34z" fill={ink}/><rect x="38" y="46" width="7" height="5" fill={blue}/><rect x="52" y="46" width="7" height="5" fill={blue}/><path d="M40 56h16" stroke={coral} strokeWidth="3" strokeLinecap="round"/><path d="M40 27V19h16v8" fill="none" stroke={ink} strokeWidth="4" strokeLinejoin="round"/><circle cx="48" cy="17" r="4" fill={yellow} stroke={ink} strokeWidth="3"/>
    {kind === 'sage' && <g><path d="M25 31 48 8l23 23z" fill={violet} stroke={ink} strokeWidth="4" strokeLinejoin="round"/><path d="M20 33h56v8H20z" fill={yellow} stroke={ink} strokeWidth="4"/><path d="m64 17 4 4m-7-9 2-5" stroke={ivory} strokeWidth="3" strokeLinecap="round"/></g>}{kind === 'coder' && <g><path d="M23 31h50l-4 12H27z" fill={blue} stroke={ink} strokeWidth="4"/><path d="M36 36h24" stroke={ivory} strokeWidth="3"/><path d="m72 61 9-9 5 5-9 9z" fill={yellow} stroke={ink} strokeWidth="3"/></g>}{kind === 'guard' && <g><path d="M73 38 88 44v17c0 11-8 18-15 22-7-4-15-11-15-22V44z" fill={blue} stroke={ink} strokeWidth="4" strokeLinejoin="round"/><path d="m73 48 3 7 7 1-5 5 1 8-6-4-7 4 2-8-6-5 8-1z" fill={yellow}/></g>}{kind === 'dj' && <g><path d="M22 47v-9c0-12 10-21 26-21s26 9 26 21v9" fill="none" stroke={coral} strokeWidth="6"/><rect x="16" y="43" width="12" height="18" rx="5" fill={coral} stroke={ink} strokeWidth="3"/><rect x="68" y="43" width="12" height="18" rx="5" fill={coral} stroke={ink} strokeWidth="3"/><path d="M73 72v-9h12" stroke={yellow} strokeWidth="4" strokeLinecap="round"/></g>}{kind === 'pilot' && <g><path d="M27 30c3-17 39-17 42 0" fill={violet} stroke={ink} strokeWidth="4"/><path d="M20 40h56" stroke={yellow} strokeWidth="5"/><path d="m75 23 9-6" stroke={blue} strokeWidth="4" strokeLinecap="round"/></g>}{kind === 'oracle' && <g><circle cx="71" cy="34" r="12" fill={yellow} stroke={ink} strokeWidth="4"/><circle cx="71" cy="34" r="5" fill={ivory}/><path d="m62 43-9 10" stroke={ink} strokeWidth="4" strokeLinecap="round"/><path d="M29 31h13" stroke={coral} strokeWidth="4"/></g>}<path d="M29 77v7m38-7v7" stroke={ink} strokeWidth="5" strokeLinecap="round"/></g></svg>;
}

/** Default player avatar used until `player.avatar` points at a real image. */
export function PlayerAvatar({ size = 104, className = '' }: { size?: number; className?: string }) {
  return <svg className={className} width={size} height={size} viewBox="0 0 96 96" role="img" aria-hidden="true"><circle cx="48" cy="48" r="44" fill={ivory} stroke={ink} strokeWidth="4"/><circle cx="48" cy="40" r="15" fill={violet} stroke={ink} strokeWidth="4"/><path d="M20 82c4-16 16-24 28-24s24 8 28 24" fill={blue} stroke={ink} strokeWidth="4" strokeLinejoin="round"/><path d="m48 12 4 8 9 1-6 6 1 9-8-4-8 4 1-9-6-6 9-1z" fill={yellow} stroke={ink} strokeWidth="2"/></svg>;
}
