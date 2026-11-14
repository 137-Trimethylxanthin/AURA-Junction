import { cn } from '@/lib/utils'

/**
 * Three-bar "sound" indicator. Animated while active; under prefers-reduced-motion the bars stand still at
 * staggered heights (a static, still-readable "on air" mark).
 */
export function VoiceLevel({ active, className }: { active: boolean; className?: string }) {
  return (
    <span aria-hidden data-voice-level={active ? 'on' : 'off'} className={cn('inline-flex h-4 items-end gap-0.5', className)}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={cn(
            'w-1 rounded-full bg-current',
            active ? 'aura-voice-bar motion-reduce:animate-none' : 'h-1 opacity-50',
            active && ['h-2', 'h-4', 'h-3'][i],
          )}
          style={active ? { animationDelay: `${i * 140}ms` } : undefined}
        />
      ))}
      <style>{`@keyframes aura-voice-bar{0%,100%{transform:scaleY(.45)}50%{transform:scaleY(1)}}
.aura-voice-bar{transform-origin:bottom;animation:aura-voice-bar .9s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){.aura-voice-bar{animation:none;transform:none}}`}</style>
    </span>
  )
}
