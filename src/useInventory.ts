import { useCallback, useEffect, useRef, useState } from 'react';
import type { Inventory } from './types';
import { t } from './i18n';

export interface SyncMeta {
  schemaVersion?: number;
  source?: string;
  revision?: number;
  contentHash?: string;
  collectedAt?: string;
  heartbeatAt?: string;
  receivedAt?: string;
  ageSeconds?: number;
  stale?: boolean;
  staleAfterSeconds?: number;
  sources?: Inventory['sources'];
  machines?: Inventory['machines'];
}

export function useInventory() {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [sync, setSync] = useState<SyncMeta | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());
  const [lastContact, setLastContact] = useState<number | null>(null);
  const state = useRef({ busy: false, hash: '', loaded: false });

  const refresh = useCallback(async () => {
    if (state.current.busy) return;
    state.current.busy = true;
    try {
      const metaResponse = await fetch('/api/inventory/meta', { cache: 'no-store', signal: AbortSignal.timeout(12000) });
      if (!metaResponse.ok) throw new Error(metaResponse.status === 401 || metaResponse.status === 403
        ? t('error.sessionExpired')
        : metaResponse.status === 404 ? t('error.waitingFirstSync') : t('error.server'));
      const metaBody: { sync: SyncMeta; inventory?: Partial<Inventory> } = await metaResponse.json();
      const meta = { ...metaBody.sync, sources: metaBody.inventory?.sources, machines: metaBody.inventory?.machines };
      if (!meta.receivedAt || !meta.contentHash) throw new Error(t('error.meta'));
      if (!state.current.loaded || meta.contentHash !== state.current.hash) {
        const response = await fetch('/api/inventory', { cache: 'no-store', signal: AbortSignal.timeout(20000) });
        if (!response.ok) throw new Error(t('error.fetch'));
        const body: { inventory: Inventory; sync: SyncMeta } = await response.json();
        if (!Array.isArray(body.inventory?.items)) throw new Error(t('error.format'));
        setInventory(body.inventory);
        setSync(body.sync);
        state.current.hash = body.sync.contentHash || meta.contentHash;
        state.current.loaded = true;
      } else {
        setSync(meta);
        setInventory(old => old ? { ...old, collectedAt: meta.collectedAt || old.collectedAt, sources: meta.sources || old.sources, machines: meta.machines || old.machines } : old);
      }
      setLastContact(Date.now());
      setError('');
    } catch (cause) {
      setError(cause instanceof Error && !['TimeoutError', 'SyntaxError', 'TypeError'].includes(cause.name) ? cause.message : t('error.connection'));
    } finally {
      state.current.busy = false;
      setLoading(false);
      setNow(Date.now());
    }
  }, []);

  useEffect(() => {
    void refresh();
    const poll = setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    const resume = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => { clearInterval(poll); clearInterval(clock); document.removeEventListener('visibilitychange', resume); window.removeEventListener('online', resume); };
  }, [refresh]);

  const heartbeatAge = sync?.heartbeatAt ? Math.max(0, (now - Date.parse(sync.heartbeatAt)) / 1000) : Infinity;
  const stale = !sync || sync.stale === true || heartbeatAge > (sync.staleAfterSeconds || 120);
  return { inventory, sync, error, loading, now, lastContact, stale, refresh };
}
