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

// A panel takes a request while its reader may hold unsaved work. The request is spent in every case, so it never replays; the record opens
// only when the reader may leave what they were doing (leave asks first and wipes the form), otherwise the panel stays as it is.
export function takeFocus<T>(
  request: FocusRequest,
  panel: {
    spend: (request: FocusRequest) => void;
    leave: () => boolean;
    open: (id: string) => T;
  },
): T | undefined {
  panel.spend(request);
  return panel.leave() ? panel.open(request.id) : undefined;
}
