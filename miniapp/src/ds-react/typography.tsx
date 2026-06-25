/**
 * Типографические helpers: Eyebrow (overline label), SerifH (Source Serif заголовки),
 * SectionLabel (small uppercase подзаголовок).
 */

import { Icon } from "./Icon";

interface EyebrowProps {
  children: React.ReactNode;
  icon?: string;
  style?: React.CSSProperties;
}

export function Eyebrow({ children, icon, style }: EyebrowProps) {
  return (
    <span className="dsx-eyebrow" style={style}>
      {icon && <Icon name={icon} size={11} />}
      <span>{children}</span>
    </span>
  );
}

interface SerifHProps {
  as?: "h1" | "h2" | "h3";
  size?: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export function SerifH({ as = "h2", size = 28, children, style, className = "" }: SerifHProps) {
  const Tag = as;
  return (
    <Tag
      className={`dsx-serif-h ${className}`.trim()}
      style={{ fontSize: size, ...style }}
    >
      {children}
    </Tag>
  );
}

interface SectionLabelProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function SectionLabel({ children, style }: SectionLabelProps) {
  return <div className="dsx-section-label" style={style}>{children}</div>;
}
