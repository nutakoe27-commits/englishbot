// Единый минимальный стиль для админки (в inline-стилях — чтобы не заводить CSS).
import type { CSSProperties } from "react";

export const colors = {
  bg: "#f5f6f8",
  card: "#ffffff",
  border: "#e5e7eb",
  text: "#1a1a1a",
  textMuted: "#6b7280",
  primary: "#2563eb",
  primaryText: "#ffffff",
  danger: "#dc2626",
  success: "#16a34a",
  warning: "#d97706",
  warningBg: "#fef3c7",
  dangerBg: "#fee2e2",
  successBg: "#dcfce7",
};

export const S: Record<string, CSSProperties> = {
  page: {
    minHeight: "100vh",
    backgroundColor: colors.bg,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif",
    color: colors.text,
  },
  header: {
    padding: "16px 24px",
    borderBottom: `1px solid ${colors.border}`,
    backgroundColor: colors.card,
    display: "flex",
    alignItems: "center",
    gap: 16,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 600,
    margin: 0,
  },
  nav: {
    display: "flex",
    gap: 8,
    flex: 1,
  },
  navLink: {
    background: "none",
    border: "none",
    padding: "6px 12px",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 14,
    color: colors.textMuted,
  },
  navLinkActive: {
    backgroundColor: "#eff6ff",
    color: colors.primary,
    fontWeight: 600,
  },
  container: {
    padding: 24,
    maxWidth: 1100,
    margin: "0 auto",
  },
  card: {
    backgroundColor: colors.card,
    borderRadius: 10,
    border: `1px solid ${colors.border}`,
    padding: 20,
    marginBottom: 16,
  },
  h2: { fontSize: 20, fontWeight: 600, margin: "0 0 12px" },
  h3: { fontSize: 15, fontWeight: 600, margin: "0 0 8px" },
  label: {
    display: "block",
    fontSize: 13,
    color: colors.textMuted,
    marginBottom: 4,
  },
  input: {
    width: "100%",
    padding: "8px 10px",
    fontSize: 14,
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    outline: "none",
    boxSizing: "border-box",
    fontFamily: "inherit",
  },
  btn: {
    padding: "8px 14px",
    fontSize: 14,
    fontWeight: 500,
    borderRadius: 6,
    border: "none",
    cursor: "pointer",
    backgroundColor: colors.primary,
    color: colors.primaryText,
  },
  btnSecondary: {
    padding: "8px 14px",
    fontSize: 14,
    fontWeight: 500,
    borderRadius: 6,
    border: `1px solid ${colors.border}`,
    cursor: "pointer",
    backgroundColor: colors.card,
    color: colors.text,
  },
  btnDanger: {
    padding: "8px 14px",
    fontSize: 14,
    fontWeight: 500,
    borderRadius: 6,
    border: "none",
    cursor: "pointer",
    backgroundColor: colors.danger,
    color: "#fff",
  },
  badge: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 10,
    fontSize: 12,
    fontWeight: 500,
  },
  metricsGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
    gap: 12,
  },
  metricCard: {
    backgroundColor: colors.card,
    border: `1px solid ${colors.border}`,
    borderRadius: 10,
    padding: "14px 16px",
  },
  metricValue: {
    fontSize: 24,
    fontWeight: 700,
    margin: 0,
    color: colors.text,
  },
  metricLabel: {
    fontSize: 12,
    color: colors.textMuted,
    textTransform: "uppercase",
    letterSpacing: 0.3,
    margin: "4px 0 0",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontSize: 14,
  },
  th: {
    textAlign: "left" as const,
    padding: "8px 10px",
    borderBottom: `1px solid ${colors.border}`,
    fontWeight: 600,
    color: colors.textMuted,
    fontSize: 12,
    textTransform: "uppercase" as const,
    letterSpacing: 0.3,
  },
  td: {
    padding: "10px",
    borderBottom: `1px solid ${colors.border}`,
  },
  rowClickable: {
    cursor: "pointer",
  },
  error: {
    backgroundColor: colors.dangerBg,
    color: colors.danger,
    padding: "10px 12px",
    borderRadius: 6,
    fontSize: 14,
    marginBottom: 12,
  },
  success: {
    backgroundColor: colors.successBg,
    color: colors.success,
    padding: "10px 12px",
    borderRadius: 6,
    fontSize: 14,
    marginBottom: 12,
  },
  muted: {
    color: colors.textMuted,
    fontSize: 13,
  },
};

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function fmtRub(n: number): string {
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

export function fmtSeconds(s: number): string {
  const min = Math.floor(s / 60);
  const sec = s % 60;
  return `${min}м ${sec}с`;
}

export function userFullName(u: {
  first_name: string | null;
  last_name: string | null;
  username: string | null;
  tg_id: number;
}): string {
  const parts = [u.first_name, u.last_name].filter(Boolean).join(" ");
  if (parts) return parts;
  if (u.username) return `@${u.username}`;
  return `tg_id ${u.tg_id}`;
}
