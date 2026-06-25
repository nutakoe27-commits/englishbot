/**
 * Button — primary/secondary/ghost/danger × sm/md/lg.
 * Имитирует v2 DS Button.jsx (mossy-green primary с warm-glow, cream secondary).
 */

import { Icon } from "./Icon";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface Props extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "size"> {
  variant?: Variant;
  size?: Size;
  icon?: string;            // имя lucide-иконки слева
  iconRight?: string;       // имя lucide-иконки справа
  fullWidth?: boolean;
  children?: React.ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  icon,
  iconRight,
  fullWidth = false,
  children,
  className = "",
  ...rest
}: Props) {
  const cls = [
    "dsx-btn",
    `dsx-btn--${variant}`,
    `dsx-btn--${size}`,
    fullWidth ? "dsx-btn--block" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <button type="button" className={cls} {...rest}>
      {icon && <Icon name={icon} size={size === "lg" ? 18 : 16} />}
      {children && <span>{children}</span>}
      {iconRight && <Icon name={iconRight} size={size === "lg" ? 18 : 16} />}
    </button>
  );
}
