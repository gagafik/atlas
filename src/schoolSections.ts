// The optional sections of the school menu. null means the setting has not arrived yet: they stay hidden, so nothing flashes.
export const sectionVisible = (hidden: string[] | null, id: string) =>
  hidden !== null && !hidden.includes(id);
