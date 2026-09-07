export function formatMoney(amount: number, currency = "EUR"): string {
  return `${currency} ${amount.toFixed(2)}`;
}

export function upperFirst(input: string): string {
  return input.charAt(0).toUpperCase() + input.slice(1);
}
