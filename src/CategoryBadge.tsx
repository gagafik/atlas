import { textColor, type Category } from "./assessmentCategories";

// The school's label of a work (ФО, СОР, СОЧ…) in its own colour.
export default function CategoryBadge({
  category,
}: {
  category: Pick<Category, "name" | "color"> & { title?: string };
}) {
  return (
    <span
      className="badge category-badge"
      title={category.title}
      style={{ background: category.color, color: textColor(category.color) }}
    >
      {category.name}
    </span>
  );
}
