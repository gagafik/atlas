export interface PlanTopic {
  id: string;
  title: string;
  hours: number;
  plannedDate: string | null;
  standard: string;
  materials?: PlanMaterialReference[];
}
export interface PlanMaterialReference {
  id: string;
  version: number;
}
export interface PlanMaterialStatus extends PlanMaterialReference {
  available: boolean;
  title?: string;
}
export interface PlanContent {
  title: string;
  groupId: string;
  subjectId: string;
  start: string;
  end: string;
  topics: PlanTopic[];
}
export interface PlanDetail {
  id: string;
  version: number;
  latestVersion: number;
  archived: boolean;
  created: string;
  actor: string;
  content: PlanContent;
  materialStatus: Record<string, PlanMaterialStatus>;
}
export interface TopicReference {
  planId: string;
  version: number;
  topicId: string;
  units: number;
  planTitle: string;
  title: string;
  standard: string;
  plannedHours: number;
}
export interface PlanProgress {
  asOf: string;
  lessonOffset: number;
  totalLessons: number;
  hasMore: boolean;
  totals: { planned: number; actual: number; review: number; upcoming: number };
  topics: {
    id: string;
    title: string;
    planned: number;
    actual: number;
    review: number;
    upcoming: number;
    retired: boolean;
  }[];
  lessons: {
    lessonKey: string;
    day: string;
    title: string;
    units: number;
    status: "actual" | "review" | "upcoming";
    version: number;
  }[];
}
