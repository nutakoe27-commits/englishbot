/**
 * Switch — 46×28 toggle с ease-spring transition.
 */

interface Props {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  "aria-label"?: string;
}

export function Switch({ checked, onChange, disabled = false, ...rest }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`dsx-switch ${checked ? "is-on" : ""}`}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      {...rest}
    >
      <span className="dsx-switch__track" />
      <span className="dsx-switch__thumb" />
    </button>
  );
}
