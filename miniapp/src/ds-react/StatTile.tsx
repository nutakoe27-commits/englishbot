/**
 * StatTile — компактный метрик-блок (иконка + value + label).
 * Используется для streak/минут/слов/медалей.
 */

import { Icon } from "./Icon";

interface Props {
  icon?: string;
  value: React.ReactNode;
  label: string;
  tone?: "default" | "streak" | "speak" | "accent" | "record";
}

export function StatTile({ icon, value, label, tone = "default" }: Props) {
  return (
    <div className={`dsx-stat dsx-stat--${tone}`}>
      <div className="dsx-stat__top">
        {icon && <Icon name={icon} size={20} />}
        <span className="dsx-stat__val">{value}</span>
      </div>
      <div className="dsx-stat__label">{label}</div>
    </div>
  );
}
