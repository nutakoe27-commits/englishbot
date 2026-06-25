/**
 * ProgressBar — tonal-track + fill. Tone: accent (default), speak, warn.
 */

interface Props {
  value: number;
  max?: number;
  tone?: "accent" | "speak" | "warn";
  height?: number;
}

export function ProgressBar({ value, max = 100, tone = "accent", height = 8 }: Props) {
  const pct = Math.max(0, Math.min(1, value / max)) * 100;
  return (
    <div className={`dsx-progress dsx-progress--${tone}`} style={{ height }}>
      <div className="dsx-progress__fill" style={{ width: `${pct}%` }} />
    </div>
  );
}
