import SchoolLogo from "./SchoolLogo";

// The letterhead of official printouts (brand book p. 25): the colour logo as the school made it with its clear space, the school name from the school settings and a thin brand line. The body below stays black on white. Adopt it by rendering it first inside the printed element (DESIGN.md, «Бланк для печати»).
export default function Letterhead({ school }: { school: string }) {
  return (
    <header className="letterhead">
      <SchoolLogo className="letterhead-logo" />
      {school && <p>{school}</p>}
    </header>
  );
}
