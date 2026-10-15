export interface TeachingFile {
  id: string;
  filename: string;
  size: number;
  created: string;
}
export interface MaterialSnapshot {
  id: string;
  version: number;
  title: string;
  subject: string;
  topics: string[];
  text: string;
  files: TeachingFile[];
}
export interface MaterialContent {
  title: string;
  subject: string;
  topics: string[];
  text: string;
  fileIds: string[];
}
export interface LibraryItem {
  id: string;
  version: number;
  title: string;
  subject: string;
  topics: string[];
  owner: string;
  visibility: "private" | "staff";
  archived: boolean;
  updated: string;
  canEdit: boolean;
}
export interface MaterialDetail {
  id: string;
  version: number;
  latestVersion: number;
  owner: string;
  visibility: "private" | "staff";
  archived: boolean;
  canEdit: boolean;
  content: MaterialContent;
  files: TeachingFile[];
  created: string;
  actor: string;
}
export function materialSnapshot(item: MaterialDetail): MaterialSnapshot {
  return {
    id: item.id,
    version: item.version,
    title: item.content.title,
    subject: item.content.subject,
    topics: item.content.topics,
    text: item.content.text,
    files: item.files,
  };
}
export function materialFileUrl(
  workspace: string,
  file: string,
  material?: { id: string; version: number },
  lessonKey?: string,
) {
  const query = new URLSearchParams({ workspace });
  if (material) {
    query.set("materialId", material.id);
    query.set("version", String(material.version));
  }
  if (lessonKey) query.set("lessonKey", lessonKey);
  return `/api/journal/material-files/${encodeURIComponent(file)}?${query}`;
}
