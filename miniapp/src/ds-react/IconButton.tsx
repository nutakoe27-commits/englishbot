/**
 * IconButton — квадратная/круглая кнопка с lucide-иконкой.
 * Размеры: sm 32 / md 40 / lg 48. Варианты: surface (cream), accent (sage-tint),
 * ghost (без фона).
 */

import { Icon } from "./Icon";

interface Props extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "size"> {
  icon: string;
  variant?: "surface" | "accent" | "ghost";
  size?: "sm" | "md" | "lg";
  round?: boolean;
  active?: boolean;
  label?: string;         // aria-label
}

export function IconButton({
  icon,
  variant = "surface",
  size = "md",
  round = false,
  active = false,
  label,
  className = "",
  ...rest
}: Props) {
  const cls = [
    "dsx-icon-btn",
    `dsx-icon-btn--${variant}`,
    `dsx-icon-btn--${size}`,
    round ? "dsx-icon-btn--round" : "",
    active ? "is-active" : "",
    className,
  ].filter(Boolean).join(" ");

  const iconSize = size === "sm" ? 16 : size === "lg" ? 22 : 20;

  return (
    <button type="button" className={cls} aria-label={label} {...rest}>
      <Icon name={icon} size={iconSize} />
    </button>
  );
}
