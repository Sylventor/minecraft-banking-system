// Monetary arithmetic stays in integer cents; Number is used only for SVG geometry.
export function cents(value: string): bigint {
  const [whole, fraction = "00"] = value.replace("-", "").split(".");
  return (
    (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"))) *
    (value.startsWith("-") ? -1n : 1n)
  );
}
export function decimal(value: bigint): string {
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? "-" : ""}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, "0")}`;
}
