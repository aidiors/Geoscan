export function radiusSamples(minimumRadius: number, maximumRadius: number): number[] {
  if (minimumRadius === maximumRadius) return [minimumRadius];
  return [minimumRadius, (minimumRadius + maximumRadius) / 2, maximumRadius];
}
