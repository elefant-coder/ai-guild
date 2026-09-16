import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, CircleHelp } from 'lucide-react';
import './PlayerStatus.css';
import { config } from './config';
import { intlLocale, t } from './i18n';
import { PlayerAvatar } from './game-art';

type TokenTotals = { input: number; output: number; cachedInput: number; total: number };
type Day = TokenTotals & { date: string };
type Stats = { schemaVersion: 1; collectedAt: string; scope: string; timezone?: string; totals: TokenTotals; today: TokenTotals; days: Day[]; providers: { id: string; name: string; totals: TokenTotals; today: TokenTotals; sessions: number }[]; coverage: { startedAt: string | null; notes: string[] } };
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
function totals(value: unknown): TokenTotals | null { if (!value || typeof value !== 'object') return null; const v = value as Record<string, unknown>, input = number(v.input), output = number(v.output), cachedInput = number(v.cachedInput), total = number(v.total); return input === null || output === null || cachedInput === null || total === null ? null : { input, output, cachedInput, total }; }
function valid(value: unknown): Stats | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>, all = totals(v.totals), today = totals(v.today);
  if (v.schemaVersion !== 1 || typeof v.scope !== 'string' || typeof v.collectedAt !== 'string' || !Number.isFinite(Date.parse(v.collectedAt)) || !all || !today || !Array.isArray(v.days)) return null;
  const days = v.days.map(day => { const tt = totals(day); return tt && typeof (day as Record<string, unknown>).date === 'string' ? { ...tt, date: (day as Record<string, unknown>).date as string } : null; }).filter((day): day is Day => !!day);
  return { schemaVersion: 1, scope: v.scope, timezone: typeof v.timezone === 'string' ? v.timezone : undefined, collectedAt: v.collectedAt, totals: all, today, days, providers: Array.isArray(v.providers) ? v.providers as Stats['providers'] : [], coverage: (v.coverage && typeof v.coverage === 'object' ? v.coverage : { startedAt: null, notes: [] }) as Stats['coverage'] };
}
const format = (value: number) => new Intl.NumberFormat(intlLocale, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const dayLabel = (date: string) => { const d = new Date(`${date}T00:00:00`); return Number.isNaN(d.getTime()) ? '' : new Intl.DateTimeFormat(intlLocale, { weekday: 'narrow' }).format(d); };
const clock = (value: string) => new Intl.DateTimeFormat(intlLocale, { hour: '2-digit', minute: '2-digit', timeZone: config.timezone || undefined }).format(new Date(value));

export function PlayerStatus({ agents, items }: { agents: number; items: number }) {
  const [stats, setStats] = useState<Stats | null>(null), [period, setPeriod] = useState<'today' | 'week'>('today'), [open, setOpen] = useState(false), [chartOpen, setChartOpen] = useState(false), [failed, setFailed] = useState(false), [avatarFailed, setAvatarFailed] = useState(false);
  useEffect(() => { let active = true, busy = false; let controller: AbortController | null = null; const read = async () => { if (busy) return; busy = true; controller = new AbortController(); const timeout = window.setTimeout(() => controller?.abort(), 8000); try { const response = await fetch('/api/stats', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal }); const parsed = valid(response.ok ? await response.json() : null); if (active) { if (parsed) setStats(parsed); setFailed(!parsed); } } catch { if (active) setFailed(true); } finally { window.clearTimeout(timeout); busy = false; } }; void read(); const timer = window.setInterval(read, 10_000); return () => { active = false; controller?.abort(); window.clearInterval(timer); }; }, []);
  const week = stats?.days.slice(-7).reduce<TokenTotals>((sum, day) => ({ input: sum.input + day.input, output: sum.output + day.output, cachedInput: sum.cachedInput + day.cachedInput, total: sum.total + day.total }), { input: 0, output: 0, cachedInput: 0, total: 0 }) ?? null;
  const shown = stats ? (period === 'today' ? stats.today : week) : null;
  const level = shown ? Math.floor(stats!.totals.total / 1_000_000) + 1 : null;
  const next = shown ? ((stats!.totals.total % 1_000_000) / 1_000_000) * 100 : 0;
  const chart = useMemo(() => stats?.days.slice(-7) ?? [], [stats]); const max = Math.max(1, ...chart.map(d => d.total));
  const player = config.player?.name || 'Player';
  const scope = stats?.scope || '—';
  const avatar = config.player?.avatar;
  return <section className="player-status" aria-label={t('player.aria', { player })}>
    <div className="player-status__top"><div className="player-identity"><div className="player-pedestal">{avatar && !avatarFailed ? <img src={avatar} alt={t('player.avatarAlt', { player })} width="104" height="104" onError={() => setAvatarFailed(true)}/> : <PlayerAvatar size={104}/>}</div><div><span className="eyebrow">{t('player.hud', { player: player.toUpperCase() })}</span><h1>{t('player.title')}</h1><p>{t('player.scope', { scope })}</p></div></div><div className="player-status__ring" style={{ background: `radial-gradient(circle,#fff 60%,transparent 62%),conic-gradient(var(--purple) 0 ${next}%,#eee8fa ${next}% 100%)` }} aria-label={level ? `Level ${level}` : t('player.counting')}><span>{level ? `Lv.${level}` : '—'}</span><small>{t('player.level')}</small></div></div>
    <div className="player-status__main"><div className="token-readout"><div className="period-switch" aria-label={t('player.periodAria')}><button className={period === 'today' ? 'selected' : ''} aria-pressed={period === 'today'} onClick={() => setPeriod('today')}>{t('player.today')}</button><button className={period === 'week' ? 'selected' : ''} aria-pressed={period === 'week'} onClick={() => setPeriod('week')}>{t('player.week')}</button></div><span className="token-label">{period === 'today' ? t('player.tokensToday') : t('player.tokensWeek')}</span><strong>{shown ? format(shown.total) : '—'}</strong><span className="token-unit">{shown ? t('player.tokens') : t('player.counting')}</span>{shown && <div className="token-parts"><span>{t('player.input', { n: format(shown.input) })}</span><span>{t('player.output', { n: format(shown.output) })}</span><span>{t('player.cache', { n: format(shown.cachedInput) })}</span></div>}</div>
      <div className="level-progress"><p className="lifetime-tokens">{t('player.lifetime')}<strong>{stats ? format(stats.totals.total) : '—'}</strong> {t('player.tokens')}</p><div><span>{t('player.nextLevel')}</span><strong>{level ? `Lv.${level + 1}` : t('player.pending')}</strong></div><div className="level-track" aria-label={level ? t('player.progressAria', { pct: Math.round(next) }) : t('player.counting')}><i style={{ width: `${next}%` }}/></div>{level && <small>{t('player.levelNote')}</small>}</div>
      <div className="collection-counts"><div><img src="/art/navigation/agents.webp" alt=""/><span><strong>{agents}</strong><small>{t('player.companions')}</small></span></div><div><img src="/art/navigation/collection.webp" alt=""/><span><strong>{items}</strong><small>{t('player.items')}</small></span></div></div>
    </div>
    <div className="hud-freshness">{stats ? <time className={failed ? 'sync-delayed' : undefined} dateTime={stats.collectedAt}>{failed ? t('player.reconnecting') : ''}{t('player.fetched', { time: clock(stats.collectedAt) })}</time> : <span role="status">{failed ? t('player.unavailable') : t('player.countingUsage')}</span>}</div>
    <button className="activity-toggle" onClick={() => setChartOpen(v => !v)} aria-expanded={chartOpen}>{t('player.last7')} <ChevronDown size={14}/></button>
    {chartOpen && <div className="activity-strip"><div className="activity-strip__title"><span>{t('player.last7')}</span>{stats ? <time className={failed ? 'sync-delayed' : undefined} dateTime={stats.collectedAt}>{failed ? t('player.reconnecting') : ''}{t('player.fetched', { time: clock(stats.collectedAt) })}</time> : <span>{failed ? t('player.unavailable') : t('player.waitingData')}</span>}</div><div className="activity-bars" aria-label={t('player.chartAria')}>{chart.length ? chart.map(day => <div key={day.date} title={`${day.date}: ${format(day.total)} ${t('player.tokens')}`}><i style={{ height: `${(day.total / max) * 46}px` }}/><small>{dayLabel(day.date)}</small></div>) : Array.from({ length: 7 }, (_, i) => <div className="empty-bar" key={i}><i/><small>—</small></div>)}</div></div>}
    <button className="status-disclosure" onClick={() => setOpen(v => !v)} aria-expanded={open}><CircleHelp size={15}/><span>{t('player.basis')}</span><ChevronDown size={15}/></button>
    {open && <div className="status-notes"><p>{t('player.basisBody', { scope })}</p>{stats?.providers.length ? <ul>{stats.providers.map(p => <li key={p.id}>{p.name}: {format(p.totals.total)} {t('player.tokens')} · {p.sessions} sessions</li>)}</ul> : null}{stats?.coverage.notes?.length ? <ul>{stats.coverage.notes.map(note => <li key={note}>{note}</li>)}</ul> : null}</div>}
  </section>;
}
