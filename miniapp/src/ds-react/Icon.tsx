/**
 * Icon — обёртка над lucide. Рендерит <i data-lucide="name"/>.
 * При commit'е React'a useLucide hook вызывает window.lucide.createIcons().
 */

import { useLucide } from "../lucide";

interface Props {
  name: string;        // имя lucide-иконки: "user-round", "house", "mic"...
  size?: number;       // px, default 20
  color?: string;      // CSS color, default — currentColor
  className?: string;
  style?: React.CSSProperties;
}

export function Icon({ name, size = 20, color, className, style }: Props) {
  useLucide(name);
  return (
    <i
      data-lucide={name}
      className={className}
      style={{ width: size, height: size, color, display: "inline-flex", ...style }}
      aria-hidden
    />
  );
}
