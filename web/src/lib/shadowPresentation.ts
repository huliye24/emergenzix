export type ShadowEquityPoint = {
  completed_at: string;
  equity: number;
};

export function buildEquityPolyline(
  decisions: ShadowEquityPoint[],
  width = 720,
  height = 220,
): string {
  const ordered = [...decisions].sort(
    (left, right) => Date.parse(left.completed_at) - Date.parse(right.completed_at),
  );
  if (!ordered.length) return "";
  const padding = 12;
  const equities = ordered.map((decision) => decision.equity);
  const minimum = Math.min(...equities);
  const maximum = Math.max(...equities);
  const span = maximum - minimum;
  if (ordered.length === 1) {
    return `${padding},${height / 2} ${width - padding},${height / 2}`;
  }
  return ordered
    .map((decision, index) => {
      const x = padding + (index / (ordered.length - 1)) * (width - padding * 2);
      const y = span === 0
        ? height / 2
        : padding + ((maximum - decision.equity) / span) * (height - padding * 2);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function shadowReturnPct(initialCash: number, equity: number): number {
  if (!(initialCash > 0) || !Number.isFinite(equity)) return 0;
  return (equity / initialCash - 1) * 100;
}
