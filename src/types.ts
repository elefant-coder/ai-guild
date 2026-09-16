export type Category = 'model' | 'cli' | 'connection' | 'automation' | 'skill' | 'harness' | 'agent';
export type Status = 'observed' | 'configured' | 'attention' | 'unknown';
export interface Evidence { source: string; detail: string; checkedAt: string }
export interface Item {
  id: string; name: string; category: Category; summary: string; tags: string[];
  status: Status; statusLabel: string; machines: string[]; runtimes: string[];
  evidence: Evidence[]; relatedIds?: string[]; command?: string;
  schedule?: { kind: string; label: string; intervalSeconds?: number; calendar?: Record<string, number> | Record<string, number>[]; loaded?: boolean; running?: boolean; lastExitCode?: number };
  meta?: Record<string, unknown>;
}
export interface Machine {
  id?: string; status?: string; statusLabel?: string; checkedAt?: string; architecture?: string; os?: string;
  jobCount?: number; loadedJobCount?: number; runningJobCount?: number; skillCount?: number; runtimes?: string[];
  reachability?: string; stale?: boolean; lastAttemptAt?: string; lastSuccessfulAt?: string;
}
export interface Inventory {
  collectedAt: string; items: Item[]; notes: string[];
  sources: { name: string; collectedAt: string; count: number; scope?: string }[];
  machines?: Record<string, Machine>;
}

export interface MachineConfig { id: string; label: string; kind: 'local' | 'ssh'; host?: string }
export interface GuildConfig {
  language: string;
  guild: { name: string; tagline: string; description: string };
  player: { name: string; avatar: string };
  timezone: string;
  machines: MachineConfig[];
  theme: {
    preset: string;
    colors: Partial<Record<'ink' | 'muted' | 'paper' | 'surface' | 'surface2' | 'line' | 'accent' | 'accentDark' | 'warm' | 'gold' | 'edge' | 'shadow', string>>;
    font: string; darkPanel: string;
  };
  art: { style: string; characterConcept: string; subjects: string[]; toolStyle: string; constraints: string; provider: string };
  agents: { featured: string[] };
  roles: Record<string, { title?: string; strengths?: string[]; exampleTask?: string }>;
  brands: Record<string, string>;
  collector: unknown;
  sync: { endpoint: string; staleAfterSeconds: number };
}
