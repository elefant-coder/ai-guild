import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import type { Category } from './types';
import { AgentPortrait } from './game-art';

export type GameIconName = 'home' | 'agents' | 'collection' | 'automation' | 'favorites' | 'map' | 'sync' | 'search' | 'guild';
export function GameIcon({ name, size = 48, className = '' }: { name: GameIconName; size?: number; className?: string }) {
  return <span className={`game-icon ${className}`} style={{ '--icon-size': `${size}px` } as CSSProperties} aria-hidden="true"><img src={`/art/navigation/${name}.webp`} alt="" width={size} height={size} draggable={false}/></span>;
}
const categoryImages: Record<Category, string[]> = {
  model: ['/art/brands/openai.png', '/art/brands/claude.ico'],
  cli: ['/art/brands/github.png', '/art/brands/cmux.png'],
  connection: ['/art/brands/slack.png', '/art/brands/figma.png'],
  skill: ['/art/tools/design.webp'], harness: ['/art/tools/harness.webp'],
  automation: ['/art/tools/automation.webp'], agent: [],
};
export function CategoryArtwork({ category }: { category: Category }) {
  if (category === 'agent') return <span className="category-artwork equipment-object" aria-hidden="true"><AgentPortrait seed="guide" size={72}/></span>;
  return <span className={`category-artwork ${categoryImages[category].length > 1 ? 'brand-stack' : 'equipment-object'}`} aria-hidden="true">{categoryImages[category].map(src => <img key={src} src={src} alt="" draggable={false}/>)}</span>;
}

// Only decorative surfaces tilt. Touch, keyboard and reduced-motion use a still surface.
export function DepthSurface({ children, className = '' }: { children: ReactNode; className?: string }) {
  const reset = (event: PointerEvent<HTMLDivElement>) => { event.currentTarget.style.removeProperty('--tilt-x'); event.currentTarget.style.removeProperty('--tilt-y'); };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'mouse' || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const element = event.currentTarget, rect = element.getBoundingClientRect();
    element.style.setProperty('--tilt-x', `${Math.max(-4, Math.min(4, (.5 - (event.clientY - rect.top) / rect.height) * 8))}deg`);
    element.style.setProperty('--tilt-y', `${Math.max(-5, Math.min(5, ((event.clientX - rect.left) / rect.width - .5) * 10))}deg`);
  };
  return <div className={`depth-surface ${className}`} onPointerMove={move} onPointerLeave={reset} onPointerCancel={reset}>{children}</div>;
}
