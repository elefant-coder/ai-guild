import React, { useEffect, useRef } from 'react';
import { BrainCircuit, ChevronRight, Clock3, Copy, FileText, GitBranch, Plug, ShieldCheck, Sparkles, Terminal, UsersRound, Workflow, X, type LucideIcon } from 'lucide-react';
import type { Category, Item, Status } from './types';
import { CharacterPortrait } from './characters';
import { GameIcon } from './GameUI';
import { config, machineLabel } from './config';
import { intlLocale, t, tl } from './i18n';

const categoryIcons: Record<Category, LucideIcon> = { model: BrainCircuit, cli: Terminal, connection: Plug, skill: Sparkles, harness: Workflow, agent: UsersRound, automation: Clock3 };
const categoryColors: Record<Category, string> = { model: 'violet', cli: 'blue', connection: 'cyan', skill: 'amber', harness: 'rose', agent: 'violet', automation: 'coral' };
export const categoryOrder: Category[] = ['model', 'cli', 'connection', 'skill', 'harness', 'agent', 'automation'];
export const categories = categoryOrder.map(id => ({ id, label: t(`category.${id}`), short: t(`category.${id}.short`), description: t(`category.${id}.desc`), icon: categoryIcons[id], color: categoryColors[id] }));
export const categoryOf = (id: Category) => categories.find(c => c.id === id)!;
export const machineName = (id: string) => machineLabel(id);
export const statusNames: Record<Status, string> = { observed: t('status.observed'), configured: t('status.configured'), attention: t('status.attention'), unknown: t('status.unknown') };

export function date(value?: string, full = false) {
  if (!value || !Number.isFinite(Date.parse(value))) return t('detail.unchecked');
  return new Intl.DateTimeFormat(intlLocale, { ...(full ? { year: 'numeric' as const, month: 'numeric' as const, day: 'numeric' as const } : {}), hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: config.timezone || undefined }).format(new Date(value));
}
export function ago(value: string | undefined, now: number) {
  if (!value || !Number.isFinite(Date.parse(value))) return t('ago.never');
  const seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000));
  if (seconds < 60) return t('ago.seconds', { n: seconds });
  if (seconds < 3600) return t('ago.minutes', { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t('ago.hours', { n: Math.floor(seconds / 3600) });
  return t('ago.days', { n: Math.floor(seconds / 86400) });
}
export const shortName = (item: Item) => item.name;
export const strengths = (item: Item): string[] => Array.isArray(item.meta?.strengths) ? item.meta.strengths.filter((x): x is string => typeof x === 'string') : [];
export const roleTitle = (item: Item) => typeof item.meta?.title === 'string' && item.meta.title ? item.meta.title : t('agent.defaultTitle');
export function matches(item: Item, query: string) {
  const text = [item.name, item.summary, ...item.tags, ...item.runtimes, ...strengths(item), roleTitle(item), categoryOf(item.category).label].join(' ').toLocaleLowerCase();
  return query.trim().toLocaleLowerCase().split(/\s+/).every(term => text.includes(term));
}
export function itemStatus(item: Item) {
  if (item.status === 'observed' && item.category === 'cli') return t('status.installed');
  if (item.category === 'automation' && item.status !== 'attention') {
    if (item.schedule?.running === true) return t('status.running');
    if (item.schedule?.loaded === true) return t('status.waiting');
    if (item.schedule?.loaded === false) return t('status.unloaded');
  }
  return statusNames[item.status];
}
export function ItemIcon({ item, large = false }: { item: Item; large?: boolean }) {
  return <CharacterPortrait item={item} size={large ? 60 : 44}/>;
}
export function StatusBadge({ item }: { item: Item }) {
  return <span className={`status-badge ${item.status}`}><span aria-hidden="true">{item.status === 'attention' ? '!' : item.status === 'unknown' ? '?' : item.status === 'observed' ? '✓' : '○'}</span>{itemStatus(item)}</span>;
}
export function SearchField({ value, onChange, label = t('search.label'), placeholder = t('search.placeholder'), autoFocus = false }: { value: string; onChange: (v: string) => void; label?: string; placeholder?: string; autoFocus?: boolean }) {
  return <label className="search-field"><GameIcon name="search" size={32}/><input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} aria-label={label} autoFocus={autoFocus} data-autofocus={autoFocus ? 'true' : undefined}/>{value && <button type="button" onClick={() => onChange('')} aria-label={t('search.clear')}><X size={19}/></button>}</label>;
}
export function ItemRow({ item, onOpen, saved = false, onSave, compact = false }: { item: Item; onOpen: () => void; saved?: boolean; onSave?: () => void; compact?: boolean }) {
  return <article className={`item-row ${compact ? 'compact' : ''} with-character`}>
    <button className="row-open" onClick={onOpen}><span className="row-character"><CharacterPortrait item={item} size={48}/></span><span className="row-copy"><span className="row-title">{item.name}</span><span className="row-description">{item.summary}</span><span className="row-meta">{categoryOf(item.category).label}<span>·</span>{item.machines.map(machineName).join(' / ') || t('detail.noMachine')}</span><span className="row-status"><StatusBadge item={item}/><ChevronRight size={18}/></span></span></button>
    {onSave && <button className={`save-button ${saved ? 'saved' : ''}`} onClick={onSave} aria-label={saved ? t('fav.remove', { name: item.name }) : t('fav.save', { name: item.name })} aria-pressed={saved}><GameIcon name="favorites" size={26}/></button>}
  </article>;
}
export function scheduleText(item: Item, full = false): string {
  const s = item.schedule; if (!s) return t('schedule.checkConfig');
  if (s.kind === 'interval' && s.intervalSeconds) { const n = s.intervalSeconds; return n % 3600 === 0 ? t('schedule.everyHours', { n: n / 3600 }) : n % 60 === 0 ? t('schedule.everyMinutes', { n: n / 60 }) : t('schedule.everySeconds', { n }); }
  if (s.kind === 'calendar' && s.calendar) {
    const entries = Array.isArray(s.calendar) ? s.calendar : [s.calendar]; const days = tl('schedule.days'); const formatted = entries.map(c => {
      const v = c as Record<string, number | string>;
      if (typeof v.at === 'string') return t('schedule.once', { date: date(v.at, true) });
      if (typeof v.expression === 'string') return t('schedule.cron', { expr: v.expression });
      const weekday = typeof v.Weekday === 'number' ? t('schedule.weekly', { day: days[v.Weekday % 7] }) : '';
      const day = typeof v.Day === 'number' ? (v.Month ? t('schedule.monthDay', { m: v.Month, d: v.Day }) : t('schedule.monthly', { d: v.Day })) : v.Month ? t('schedule.month', { m: v.Month }) : '';
      const time = v.Hour !== undefined ? `${String(v.Hour).padStart(2, '0')}:${v.Minute !== undefined ? String(v.Minute).padStart(2, '0') : '**'}` : v.Minute !== undefined ? t('schedule.hourly', { m: v.Minute }) : t('schedule.everyMinute');
      return `${weekday}${day}${!weekday && !day && v.Hour !== undefined ? t('schedule.daily') : ''}${time}`;
    }); return (full ? formatted : formatted.slice(0, 2)).join(' / ') + (!full && formatted.length > 2 ? t('schedule.more', { n: formatted.length - 2 }) : '');
  }
  return s.kind === 'resident' ? t('schedule.resident') : s.kind === 'ondemand' ? t('schedule.ondemand') : s.label;
}
export function JobRow({ item, onOpen }: { item: Item; onOpen: () => void }) {
  return <button className="job-row" onClick={onOpen}><span className="job-clock"><CharacterPortrait item={item} size={44}/></span><span className="job-copy"><strong>{item.name}</strong><span>{item.summary}</span><small>{item.machines.map(machineName).join(' / ')}</small></span><span className="job-timing"><strong>{scheduleText(item)}</strong><StatusBadge item={item}/></span><ChevronRight size={18}/></button>;
}
export function Modal({ children, onClose, label }: { children: React.ReactNode; onClose: () => void; label: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; ref.current?.showModal(); ref.current?.querySelector<HTMLInputElement>('input[data-autofocus="true"]')?.focus(); const old = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = old; previous?.focus(); }; }, []);
  return <dialog ref={ref} className="modal" aria-label={label} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === e.currentTarget) { const r = e.currentTarget.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) onClose(); } }}><div className="modal-toolbar"><span>{t('detail.toolbar')}</span><button className="icon-button" onClick={onClose} aria-label={t('detail.close')} autoFocus><X size={24}/></button></div><div className="modal-content">{children}</div></dialog>;
}
export function agentPrompt(item: Item) {
  return t('copy.prompt.agent', { name: item.name, task: String(item.meta?.exampleTask || t('copy.prompt.defaultTask')) });
}
export function ItemDetail({ item, items, onClose, onSelect, onCopy, onSave, saved }: { item: Item; items: Item[]; onClose: () => void; onSelect: (i: Item) => void; onCopy: (s: string) => void; onSave: () => void; saved: boolean }) {
  const prompt = item.category === 'agent' ? agentPrompt(item) : t('copy.prompt.item', { name: item.name });
  return <Modal label={item.name} onClose={onClose}>
    <div className="detail-intro"><ItemIcon item={item} large/><span>{categoryOf(item.category).label}</span><button className={`icon-button ${saved ? 'saved' : ''}`} onClick={onSave} aria-label={saved ? t('fav.removeShort') : t('fav.saveShort')} aria-pressed={saved}><GameIcon name="favorites" size={33}/></button></div>
    <div className="detail-portrait"><CharacterPortrait item={item} size={215}/></div><h2 className="detail-name">{item.name}</h2>{item.category === 'agent' && <p className="partner-class">{roleTitle(item)}</p>}<p className="detail-summary">{item.summary}</p><StatusBadge item={item}/>
    {item.category === 'agent' && <section className="detail-section"><h3><Sparkles size={18}/>{t('detail.strengths')}</h3><div className="tag-list">{strengths(item).map(s => <span key={s}>{s}</span>)}</div><h3>{t('detail.example')}</h3><p className="example-text">{String(item.meta?.exampleTask || '')}</p><button className="primary" onClick={() => onCopy(prompt)}><Copy size={18}/>{t('detail.copyExample')}</button></section>}
    <dl className="facts"><div><dt>{t('detail.machine')}</dt><dd>{item.machines.map(machineName).join(' / ') || t('detail.unrecorded')}</dd></div><div><dt>{t('detail.route')}</dt><dd>{item.runtimes.join(' / ') || t('detail.seeEvidence')}</dd></div><div><dt>{t('detail.state')}</dt><dd>{item.statusLabel}</dd></div></dl>
    {item.schedule && <section className="detail-section"><h3><Clock3 size={18}/>{t('detail.schedule')}</h3><p className="schedule-large">{scheduleText(item, true)}</p>{item.schedule.calendar && JSON.stringify(item.schedule.calendar).includes('expression') && <p className="muted">{t('detail.cronNote')}</p>}<dl className="facts"><div><dt>{t('detail.loaded')}</dt><dd>{item.schedule.loaded === true ? t('detail.loadedYes') : item.schedule.loaded === false ? t('detail.loadedNo') : t('detail.unchecked')}</dd></div><div><dt>{t('detail.running')}</dt><dd>{item.schedule.running === true ? t('detail.runningYes') : item.schedule.running === false ? t('detail.runningNo') : t('detail.unchecked')}</dd></div>{item.schedule.lastExitCode !== undefined && <div><dt>{t('detail.exit')}</dt><dd>{item.schedule.lastExitCode === 0 ? t('detail.exitOk') : t('detail.exitBad', { code: item.schedule.lastExitCode })}</dd></div>}</dl></section>}
    {item.relatedIds?.some(id => items.some(i => i.id === id)) && <section className="detail-section"><h3><GitBranch size={18}/>{t('detail.related')}</h3><div className="related-list">{item.relatedIds.map(id => items.find(i => i.id === id)).filter((i): i is Item => !!i).map(i => <button key={i.id} onClick={() => onSelect(i)}><ItemIcon item={i}/><span>{i.name}</span><ChevronRight size={18}/></button>)}</div></section>}
    {item.category === 'agent' && typeof item.meta?.modelPolicy === 'string' && <section className="detail-section"><h3>{t('detail.modelSetting')}</h3><p>{item.meta.modelPolicy}</p><p className="muted">{t('detail.modelNote')}</p></section>}
    <details className="evidence-section"><summary><ShieldCheck size={18}/>{t('detail.evidence')}<span>{t('detail.evidenceCount', { n: item.evidence.length })}</span></summary>{item.evidence.map((e, index) => <div className="evidence" key={index}><p>{e.detail}</p><code>{e.source}</code><time>{date(e.checkedAt, true)}</time></div>)}</details>
    {item.command && <section className="detail-section"><h3><Terminal size={18}/>{t('detail.command')}</h3><div className="command-box"><code>{item.command}</code><button className="icon-button" onClick={() => onCopy(item.command!)} aria-label={t('detail.copyCommand')}><Copy size={18}/></button></div></section>}
    {typeof item.meta?.descriptionOriginal === 'string' && <details className="evidence-section"><summary><FileText size={18}/>{t('detail.original')}</summary><p className="original-text">{item.meta.descriptionOriginal}</p></details>}
    {typeof item.meta?.toolCount === 'number' && <p className="muted">{t('detail.tools', { n: item.meta.toolCount })}</p>}
    <div className="detail-footer"><button className="primary" onClick={() => onCopy(prompt)}><Copy size={18}/>{t('detail.copyPrompt')}</button><span>{t('detail.copyNote')}</span></div>
  </Modal>;
}
