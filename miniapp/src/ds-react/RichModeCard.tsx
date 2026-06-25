/**
 * RichModeCard — большая карточка режима на главной (Speaking/Listening/Grammar/Vocab).
 * Layout: icon-pill сверху-слева + eyebrow + title + subtitle (слева),
 * arrow-chip сверху-справа, illustration BG в правом нижнем углу (faded).
 */

import { Icon } from "./Icon";

interface Props {
  icon: string;            // lucide-имя
  title: string;
  subtitle: string;
  eyebrow?: string;
  illustration?: string;   // путь к PNG из /public/illustrations
  tone?: "accent" | "speak" | "warn" | "record";
  onClick: () => void;
}

export function RichModeCard({
  icon,
  title,
  subtitle,
  eyebrow,
  illustration,
  tone = "accent",
  onClick,
}: Props) {
  return (
    <button
      type="button"
      className={`dsx-rich-mode dsx-rich-mode--${tone}`}
      onClick={onClick}
    >
      {illustration && (
        <span
          className="dsx-rich-mode__illust"
          style={{ backgroundImage: `url(${illustration})` }}
          aria-hidden
        />
      )}
      <span className="dsx-rich-mode__icon" aria-hidden>
        <Icon name={icon} size={20} />
      </span>
      <span className="dsx-rich-mode__text">
        {eyebrow && <span className="dsx-rich-mode__eyebrow">{eyebrow}</span>}
        <span className="dsx-rich-mode__title">{title}</span>
        <span className="dsx-rich-mode__subtitle">{subtitle}</span>
      </span>
      <span className="dsx-rich-mode__arrow" aria-hidden>
        <Icon name="arrow-right" size={16} />
      </span>
    </button>
  );
}
