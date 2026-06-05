import { useEffect, useState } from "react";

// Реактивный матчер брейкпоинта. Inline-стили нельзя завязать на media-queries,
// поэтому мобильные раскладки переключаем в JS по этому хуку.
// 760px — граница «узкий экран» (телефоны в портрете и часть планшетов).
const MOBILE_QUERY = "(max-width: 760px)";

export function useIsMobile(): boolean {
  const get = () =>
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(MOBILE_QUERY).matches;

  const [isMobile, setIsMobile] = useState<boolean>(get);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    // addEventListener — современный API; addListener — фолбэк для старых WebView.
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", onChange);
      else mql.removeListener(onChange);
    };
  }, []);

  return isMobile;
}
