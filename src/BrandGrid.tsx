import type { CSSProperties } from "react";

// The brand book's modular grid (p. 15-16): square modules in the brand's dark blue, blue and white divided by thin lines, a quiet decoration on the two non-dense screens (login, hall).
// Squares only: never the «Q» sign or any part of the logo (DESIGN.md). It stays out from under text.
const FILL: Record<string, string> = {
  n: "navy",
  b: "blue",
  w: "white",
  d: "deep",
};

export default function BrandGrid({
  rows,
  className = "",
}: {
  rows: readonly string[];
  className?: string;
}) {
  const style: CSSProperties = {
    gridTemplateColumns: `repeat(${rows[0]?.length ?? 0}, var(--module))`,
  };
  return (
    <div
      className={`brand-grid ${className}`.trim()}
      style={style}
      aria-hidden="true"
    >
      {rows.flatMap((row, y) =>
        [...row].map((c, x) => (
          <span
            key={`${y}.${x}`}
            className={`brand-grid-${FILL[c] ?? "white"}`}
          />
        )),
      )}
    </div>
  );
}

export const HALL_GRID = ["nbdnbw", "bwnbdn"];

// The sign-in screen: a block in the top right and the bottom left corners on a computer, one band above the logo on a phone or tablet.
export function LoginGrid() {
  return (
    <>
      <BrandGrid
        className="login-grid login-grid-top"
        rows={["wnb", "nbw", "bwn", "wnw"]}
      />
      <BrandGrid
        className="login-grid login-grid-bottom"
        rows={["nwb", "bnw"]}
      />
      <BrandGrid
        className="login-grid login-grid-band"
        rows={["nbwnwbnnbwbnwn"]}
      />
    </>
  );
}
