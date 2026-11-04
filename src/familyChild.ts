// The child the family portal shows. A parent with several children chooses one in the account menu and every family
// screen follows; the choice is kept per browser. No React here, so the rules run in node tests.
export interface ChildChoice {
  id: string;
  name: string;
  className?: string | null;
}

/** The child on show: the wanted one while the account still has it, else the first child the server listed, else none.
 *  A child that was unlinked from the account since the choice was kept is never shown. */
export const currentChild = (children: ChildChoice[], wanted?: string | null) =>
  children.find((child) => child.id === wanted)?.id ?? children[0]?.id ?? "";

/** The name of a child as the list shows it: with the class when there is one. */
export const childLabel = (child: ChildChoice) =>
  child.className ? `${child.name}, ${child.className}` : child.name;

// The choice is remembered per browser and workspace; storage that is blocked or empty means the first child.
const KEY = "atlas:child:";
export function recallChoice(
  workspace: string,
  storage?: Pick<Storage, "getItem">,
): string | undefined {
  try {
    return (storage ?? localStorage).getItem(KEY + workspace) || undefined;
  } catch {
    return undefined;
  }
}
export function rememberChoice(
  workspace: string,
  child: string,
  storage?: Pick<Storage, "setItem">,
) {
  try {
    (storage ?? localStorage).setItem(KEY + workspace, child);
  } catch {
    // Not remembered; the first child is shown next time.
  }
}
