/**
 * Badge — маленькая pill для статус-меток.
 */

import { Icon } from "./Icon";

interface Props {
  tone?: "accent" | "speak" | "record" | "warn" | "neutral";
  icon?: string;
  children?: React.ReactNode;
}

export function Badge({ tone = "neutral", icon, children }: Props) {
  return (
    <span className={`dsx-badge dsx-badge--${tone}`}>
      {icon && <Icon name={icon} size={12} />}
      <span>{children}</span>
    </span>
  );
}
