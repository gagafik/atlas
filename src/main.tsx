import React from "react";
import ReactDOM from "react-dom/client";
import AuthShell from "./AuthShell";
import "./styles.css";
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
    <AuthShell />
  </React.StrictMode>,
);
