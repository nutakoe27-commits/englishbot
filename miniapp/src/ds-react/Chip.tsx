/**
 * Chip — pill-фильтр (34h, padding 0 14). Active = sage-tint фон + accent border.
 */

import { Icon } from "./Icon";

interface Props extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  active?: boolean;
  icon?: string;
  children?: React.ReactNode;
}

export function Chip({ active = false, icon, children, className = "", ...rest }: Props) {
  return (
    <button
      type="button"
      className={`dsx-chip ${active ? "is-active" : ""} ${className}`.trim()}
      data-active={active ? "true" : "false"}
      {...rest}
    >
      {icon && <Icon name={icon} size={14} />}
      <span>{children}</span>
    </button>
  );
}
