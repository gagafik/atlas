// The school's logo as the school made it (public/brand, see DESIGN.md): the colour version for white, the white one for dark blue.
// Never recolour, crop, stretch or put effects on it; the caller keeps clear space of half the sign around it and sets the height with CSS (28 px at least).
const FILES = {
  color: { name: "quantum-stem-logo-color.png", width: 931, height: 325 },
  white: { name: "quantum-stem-logo-white.png", width: 931, height: 324 },
};

export default function SchoolLogo({
  on = "white",
  className = "",
}: {
  /** The background the logo stands on: "white" takes the colour version, "blue" (the dark brand blue) the white one. */
  on?: "white" | "blue";
  className?: string;
}) {
  const file = FILES[on === "blue" ? "white" : "color"];
  return (
    <img
      className={`school-logo ${className}`.trim()}
      src={`${import.meta.env.BASE_URL}brand/${file.name}`}
      width={file.width}
      height={file.height}
      alt="Quantum STEM School"
    />
  );
}
