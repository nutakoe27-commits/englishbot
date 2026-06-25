/**
 * NoteCard — основной контейнер карточки в v2 ("notebook on grid paper").
 * Soft border + warm cream fill + subtle shadow. Tone задаёт оттенок.
 */

interface Props extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "default" | "sage" | "cream" | "warn";
  padding?: number;
  interactive?: boolean;
  children?: React.ReactNode;
}

export function NoteCard({
  tone = "default",
  padding = 18,
  interactive = false,
  children,
  className = "",
  style,
  ...rest
}: Props) {
  const cls = [
    "dsx-card",
    `dsx-card--${tone}`,
    interactive ? "dsx-card--interactive" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <div className={cls} style={{ padding, ...style }} {...rest}>
      {children}
    </div>
  );
}
