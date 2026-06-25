/**
 * LogoBox — sage квадрат с «En» в italic serif. Логотип-марка для headers.
 */

interface Props {
  size?: number;
}

export function LogoBox({ size = 36 }: Props) {
  return (
    <span
      className="dsx-logo-box"
      style={{ width: size, height: size }}
      aria-hidden
    >
      En
    </span>
  );
}
