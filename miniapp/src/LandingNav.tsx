/**
 * LandingNav.tsx — общая шапка публичных лендингов (основного и школьного).
 *
 * Зачем общий компонент: пунктов в меню стало больше, чем помещается в строку
 * на телефоне, и каждый лендинг ломался по-своему. Здесь одна логика:
 *   • десктоп — привычная строка со ссылками и кнопкой;
 *   • мобильный — бренд, переключатель темы и бургер; ссылки уезжают в
 *     выдвижную панель с крупными зонами нажатия.
 *
 * Панель закрывается по фону, Esc, выбору пункта и при переходе на десктопную
 * ширину; пока открыта — фон не прокручивается.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ThemeToggle } from "./ThemeToggle";

export interface NavItem {
  label: string;
  href?: string;
  onClick?: () => void;
}

interface Props {
  /** Маленькая плашка рядом с названием — например «для школ». */
  badge?: string;
  /** Куда ведёт логотип. По умолчанию на главную. */
  brandHref?: string;
  items: NavItem[];
  cta: NavItem;
}

export function LandingNav({ badge, brandHref = "/", items, cta }: Props) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Пока панель открыта: блокируем прокрутку фона и слушаем Esc.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Растянули окно до десктопа — панель больше не нужна.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 761px)");
    const onChange = () => { if (mq.matches) setOpen(false); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const run = (item: NavItem) => {
    setOpen(false);
    if (item.onClick) item.onClick();
    else if (item.href) window.location.href = item.href;
  };

  const renderItem = (item: NavItem, cls: string) =>
    item.href && !item.onClick ? (
      <a key={item.label} className={cls} href={item.href} onClick={() => setOpen(false)}>
        {item.label}
      </a>
    ) : (
      <button key={item.label} type="button" className={cls} onClick={() => run(item)}>
        {item.label}
      </button>
    );

  return (
    <header className="lp-header">
      <div className="lp-container lp-header__inner">
        <a className="lp-brand lp-brand--link" href={brandHref}>
          <span className="lp-brand__dot" aria-hidden />
          <span className="lp-brand__name">
            English&nbsp;Tutor
            {badge && <span className="sch-badge">{badge}</span>}
          </span>
        </a>

        {/* Десктоп: всё в одну строку. */}
        <nav className="lp-nav lp-nav--desktop">
          <ThemeToggle />
          {items.map((it) => renderItem(it, "lp-nav__link"))}
          {renderItem(cta, "lp-btn lp-btn--primary lp-btn--sm")}
        </nav>

        {/* Мобильный: тема остаётся на виду, остальное — под бургером. */}
        <div className="lp-mobile-actions">
          <ThemeToggle />
          <button
            type="button"
            className="lp-burger"
            aria-label={open ? "Закрыть меню" : "Открыть меню"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <span className={`lp-burger__box ${open ? "is-open" : ""}`}>
              <i /><i /><i />
            </span>
          </button>
        </div>
      </div>

      {/* Портал в body обязателен: у .lp-header стоит backdrop-filter, а он
          делает элемент контейнером для position:fixed потомков — панель
          позиционировалась бы относительно шапки, а не окна. */}
      {open && createPortal(
        <>
          <div className="lp-drawer__backdrop" onClick={() => setOpen(false)} />
          <div className="lp-drawer" ref={panelRef} role="dialog" aria-modal="true" aria-label="Меню">
            <div className="lp-drawer__head">
              <span className="lp-drawer__title">Меню</span>
              <button
                type="button"
                className="lp-drawer__close"
                aria-label="Закрыть меню"
                onClick={() => setOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="lp-drawer__items">
              {items.map((it) => renderItem(it, "lp-drawer__link"))}
            </div>
            <div className="lp-drawer__foot">
              {renderItem(cta, "lp-btn lp-btn--primary lp-btn--md lp-drawer__cta")}
            </div>
          </div>
        </>,
        document.body,
      )}
    </header>
  );
}
