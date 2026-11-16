import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BehaviourNoteForm from "./BehaviourNoteForm";
import type { NoteForm, Options } from "./behaviourNotesModel";

const options = {
  admin: false,
  canWrite: true,
  today: "2026-11-17",
  levels: { teacher: "Учитель", class_teacher: "Классный", director: "Адм." },
  myLevels: ["teacher"],
  categories: [],
  classes: [{ id: "c7a", name: "7А" }],
  students: [
    { id: "s1", name: "Примеров Данияр", classId: "c7a", className: "7А" },
    { id: "s2", name: "Вымыслов Артём", classId: "c7a", className: "7А" },
  ],
} as unknown as Options;
const form = (over: Partial<NoteForm> = {}): NoteForm => ({
  categoryId: "",
  day: "2026-11-17",
  text: "",
  classId: "",
  visible: true,
  printed: false,
  students: [],
  ...over,
});
const render = (over: { error?: string; notice?: string; form?: NoteForm }) =>
  renderToStaticMarkup(
    <BehaviourNoteForm
      options={options}
      form={over.form ?? form()}
      setForm={() => {}}
      usable={[]}
      quick={[]}
      course={null}
      forCourse
      setForCourse={() => {}}
      error={over.error ?? ""}
      notice={over.notice ?? ""}
      onSubmit={() => {}}
      onBack={() => {}}
    />,
  );

describe("BehaviourNoteForm", () => {
  it("shows a refusal and a result inside the form, by its buttons", () => {
    const markup = render({
      error: "Запись изменена другим пользователем",
      notice: "Запись сохранена",
    });
    const messages = markup.indexOf("Запись изменена другим пользователем");
    expect(messages).toBeGreaterThan(-1);
    expect(markup).toContain("Запись сохранена");
    // The messages come after the fields and before the buttons that were pressed.
    expect(messages).toBeGreaterThan(markup.indexOf("<textarea"));
    expect(messages).toBeLessThan(markup.indexOf("Сохранить"));
  });

  it("shows no message when there is none", () => {
    const markup = render({});
    expect(markup).not.toContain('role="alert"');
    expect(markup).not.toContain('role="status"');
  });

  it("keeps the chosen pupils in view with their class", () => {
    const markup = render({ form: form({ students: ["s2"] }) });
    expect(markup).toContain("Вымыслов Артём · 7А");
  });
});
