/** Freeze `new Date()`, `Date.now()` and `Date()` at the instant of the capture. */
export function installClock(
  capturedAt: string,
  target: { Date: DateConstructor } = globalThis,
): () => void {
  const RealDate = target.Date;
  const frozenMs = RealDate.parse(capturedAt);
  if (Number.isNaN(frozenMs)) throw new Error("Bad demo snapshot date.");
  const frozenText = new RealDate(frozenMs).toString();
  const FrozenDate = new Proxy(RealDate, {
    construct(real, args, newTarget) {
      return Reflect.construct(
        real,
        args.length ? args : [frozenMs],
        newTarget,
      );
    },
    apply() {
      return frozenText;
    },
    get(real, property) {
      if (property === "now") return () => frozenMs;
      return Reflect.get(real, property, real);
    },
  });
  target.Date = FrozenDate;
  return () => {
    target.Date = RealDate;
  };
}
