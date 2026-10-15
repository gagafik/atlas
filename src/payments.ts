export function parseMoney(text: string, decimals: number): number {
  const raw = text.trim().replace(",", ".");
  const pattern = decimals
    ? new RegExp(`^\\d{1,13}(?:\\.\\d{1,${decimals}})?$`)
    : /^\d{1,13}$/;
  if (!pattern.test(raw))
    throw new Error(
      "Введите сумму без разделителей тысяч и лишних знаков после запятой.",
    );
  const [whole, fraction = ""] = raw.split(".");
  const amount =
    BigInt(whole) * 10n ** BigInt(decimals) +
    BigInt(fraction.padEnd(decimals, "0") || "0");
  if (amount > 1000000000000n)
    throw new Error("Сумма превышает допустимый предел.");
  return Number(amount);
}
export function moneyInput(value: string | number, decimals: number): string {
  const amount = BigInt(value),
    negative = amount < 0n,
    absolute = negative ? -amount : amount;
  const raw = absolute.toString().padStart(decimals + 1, "0");
  return (
    (negative ? "-" : "") +
    (decimals ? raw.slice(0, -decimals) + "." + raw.slice(-decimals) : raw)
  );
}
export function formatMoney(
  value: string | number,
  decimals: number,
  currency: string,
): string {
  const raw = moneyInput(value, decimals),
    [whole, fraction] = raw.split(".");
  const grouped = new Intl.NumberFormat("ru-RU").format(BigInt(whole));
  const prefix = raw.startsWith("-") && BigInt(whole) === 0n ? "−" : "";
  return prefix + grouped + (fraction ? "," + fraction : "") + " " + currency;
}
export const paymentKinds: Record<string, string> = {
  charge: "Начисление",
  adjustment: "Изменение начисления",
  receipt: "Поступление",
  refund: "Возврат",
  correction: "Исправление поступления",
};
export const summaryLabels: Record<string, string> = {
  charged: "Начислено",
  received: "Оплачено за вычетом возвратов",
  outstanding: "Начисления к погашению",
  unallocated: "Свободная оплата",
  balance: "Итоговый баланс",
  overdue: "Просроченные начисления",
};
export type FinanceRun = (
  path: string,
  body: unknown,
  method?: string,
) => Promise<any>;
export type FinanceFormat = (value: string | number) => string;
