// A notification asks a panel to show one record. Every request is a new object and is spent once the panel has taken it:
// the same entry opened a second time still reaches the panel, and a panel mounted later (another workspace) does not replay an old request.
export type FocusRequest = { id: string };

export const focusRequest = (
  id: string | undefined,
): FocusRequest | undefined => (id ? { id } : undefined);

// Clears the request the panel has taken, but never a newer one that arrived meanwhile.
export const spendFocus = (
  current: FocusRequest | undefined,
  spent: FocusRequest,
): FocusRequest | undefined => (current === spent ? undefined : current);
