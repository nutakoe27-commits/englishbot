/**
 * ProviderIcons.tsx — круглые кнопки входа через сервисы VK ID:
 * ВКонтакте, Одноклассники, Mail.ru. Все три идут через одно приложение
 * VK ID (backend/app/auth.py: vk_authorize_url, параметр provider).
 */

import type { VkVia } from "./auth";

export const VK_VIA_LABEL: Record<VkVia, string> = {
  vk: "ВКонтакте",
  ok: "Одноклассники",
  mail: "Mail.ru",
};

/** Фирменные знаки в упрощённом виде: цвет бренда + белый знак. */
export function VkViaMark({ via, size = 26 }: { via: VkVia; size?: number }) {
  if (via === "vk") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
        <rect width="24" height="24" rx="7" fill="#0077FF" />
        <path
          fill="#fff"
          d="M12.77 17.3c-5.22 0-8.2-3.58-8.32-9.53h2.62c.08 4.37 2.01 6.22 3.53 6.6V7.77h2.46v3.77c1.5-.16 3.08-1.88 3.62-3.77h2.46a7.27 7.27 0 0 1-3.35 4.75 7.55 7.55 0 0 1 3.92 4.78h-2.71c-.58-1.82-2.03-3.22-3.94-3.41v3.41h-.29Z"
        />
      </svg>
    );
  }
  if (via === "ok") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
        <rect width="24" height="24" rx="7" fill="#EE8208" />
        <circle cx="12" cy="8.2" r="3" fill="none" stroke="#fff" strokeWidth="2.2" />
        <path d="M8.2 12.9c2.3 1.5 5.3 1.5 7.6 0" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
        <path d="M12 14.4 8.9 18M12 14.4 15.1 18" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="7" fill="#005FF9" />
      <circle cx="12" cy="12" r="3.1" fill="none" stroke="#fff" strokeWidth="2.1" />
      <path
        d="M15.1 9.3v3.6c0 1.2.8 1.9 1.7 1.9 1.3 0 2-1.2 2-3.1A6.8 6.8 0 1 0 12 18.8c1.4 0 2.6-.4 3.6-1"
        fill="none" stroke="#fff" strokeWidth="2.1" strokeLinecap="round"
      />
    </svg>
  );
}

/** Ряд из трёх круглых кнопок. disabled — пока идёт запуск входа. */
export function VkViaRow({
  onPick, disabled = false, size = 52,
}: { onPick: (via: VkVia) => void; disabled?: boolean; size?: number }) {
  return (
    <div className="vkvia-row">
      {(["vk", "ok", "mail"] as const).map((via) => (
        <button
          key={via}
          type="button"
          className="vkvia-btn"
          style={{ width: size, height: size }}
          onClick={() => onPick(via)}
          disabled={disabled}
          aria-label={`Войти через ${VK_VIA_LABEL[via]}`}
          title={VK_VIA_LABEL[via]}
        >
          <VkViaMark via={via} size={Math.round(size * 0.6)} />
        </button>
      ))}
    </div>
  );
}
