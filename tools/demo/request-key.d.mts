export function canonicalJson(value: unknown): string;
export function pathKey(method: string, url: URL): string;
export function getKey(url: URL): string;
export function postKey(
  url: URL,
  bodyText: string | null | undefined,
  anyBody?: boolean,
): string;
