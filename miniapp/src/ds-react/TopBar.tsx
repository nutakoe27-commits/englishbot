/**
 * TopBar — стандартный header экрана. back / logo / title / right-actions.
 */

import { Icon } from "./Icon";

interface Props {
  title?: string;
  onBack?: () => void;
  right?: React.ReactNode;
  dot?: boolean;            // акцент-точка перед title
  logo?: boolean;           // показать LogoBox перед title
}

export function TopBar({ title, onBack, right, dot, logo }: Props) {
  return (
    <header className="dsx-topbar">
      {onBack && (
        <button
          type="button"
          className="dsx-icon-btn dsx-icon-btn--ghost dsx-icon-btn--md"
          onClick={onBack}
          aria-label="Назад"
        >
          <Icon name="arrow-left" size={20} />
        </button>
      )}
      {logo && <span className="dsx-logo-mark" aria-hidden>En</span>}
      <div className="dsx-topbar__title">
        {dot && <span className="dsx-dot" aria-hidden />}
        {title && <span>{title}</span>}
      </div>
      {right && <div className="dsx-topbar__right">{right}</div>}
    </header>
  );
}
