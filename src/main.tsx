import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
const AuthShell = React.lazy(() => import("./AuthShell"));
const ApplicantPortal = React.lazy(() => import("./ApplicantPortal"));
const HallDisplay = React.lazy(() => import("./HallDisplay"));
const applicationEntry = new URLSearchParams(window.location.search);
if (
  import.meta.env.PROD &&
  "serviceWorker" in navigator &&
  window.isSecureContext
) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .catch(() => {
        // Online operation remains available when this browser disables installation.
      });
  });
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <React.Suspense
      fallback={<main className="auth-screen">Открываю Атлас…</main>}
    >
      {applicationEntry.has("hall") ? (
        <HallDisplay />
      ) : applicationEntry.has("apply") ? (
        <ApplicantPortal
          initialCampaign={applicationEntry.get("apply") || null}
        />
      ) : (
        <AuthShell />
      )}
    </React.Suspense>
  </React.StrictMode>,
);
