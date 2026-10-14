export type Role =
  "admin" | "editor" | "teacher" | "student" | "parent" | "viewer";
export type AccessView = "class" | "teacher" | "room" | "group" | "student";
export interface Grant {
  workspace: "school" | "demo";
  view: AccessView;
  entityIds: string[];
}
export interface Account {
  id: string;
  name: string;
  login: string;
  role: Role;
  active: boolean;
  version: number;
  workspaces: string[];
  grants: Grant[];
}
export interface AuthStatus {
  enabled: boolean;
  user: Account | null;
  canSetup?: boolean;
}
export const roleNames: Record<Role, string> = {
  admin: "Администратор",
  editor: "Составитель",
  teacher: "Учитель",
  student: "Ученик",
  parent: "Родитель",
  viewer: "Наблюдатель",
};
