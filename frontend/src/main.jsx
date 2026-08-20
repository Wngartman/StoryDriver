import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { initializeUiPreset } from "./ui-presets/applyUiPreset.js";
import { API_BASE_URL } from "./api.js";

function bootMessage(error = null) {
  const detail = error?.message || String(error || "");
  return `
    <div style="min-height:100vh;background:#07080a;color:#f4efe7;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;place-items:center;padding:24px;">
      <div style="width:min(680px,100%);border:1px solid rgba(148,163,184,.28);background:rgba(15,17,21,.94);box-shadow:0 24px 80px rgba(0,0,0,.42);border-radius:18px;padding:24px;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:.16em;color:#93c5fd;margin-bottom:10px;">StoryDriver</div>
        <h1 style="margin:0 0 10px;font-size:22px;line-height:1.2;">StoryDriver frontend loaded, but the app could not finish starting.</h1>
        <p style="margin:0 0 14px;color:#cbd5e1;line-height:1.6;">This usually means the browser could not load the local frontend modules or the backend is unreachable from this device.</p>
        <div style="display:grid;gap:8px;color:#d6d3d1;font-size:14px;line-height:1.5;">
          <div><strong>Detected backend:</strong> <code style="color:#bae6fd;word-break:break-all;">${API_BASE_URL}</code></div>
          <div><strong>Health check:</strong> <code style="color:#bae6fd;word-break:break-all;">${API_BASE_URL}/health</code></div>
          <div><strong>Next:</strong> open the health check from this device, confirm Windows Firewall allows private-network access for Node.js and Python, then refresh StoryDriver.</div>
        </div>
        ${detail ? `<pre style="margin:16px 0 0;white-space:pre-wrap;word-break:break-word;color:#fca5a5;background:rgba(127,29,29,.16);border:1px solid rgba(248,113,113,.25);border-radius:12px;padding:12px;font-size:12px;line-height:1.5;">${detail.replace(/[<>&]/g, (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[char])}</pre>` : ""}
      </div>
    </div>
  `;
}

function renderBootMessage(error = null) {
  const root = document.getElementById("root");
  if (root) {
    root.innerHTML = bootMessage(error);
  }
}

class StoryDriverErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error("StoryDriver render failed", error);
  }

  render() {
    if (this.state.error) {
      return <div dangerouslySetInnerHTML={{ __html: bootMessage(this.state.error) }} />;
    }
    return this.props.children;
  }
}

window.__STORYDRIVER_FRONTEND_BOOTED = false;
window.addEventListener("error", (event) => {
  if (!window.__STORYDRIVER_FRONTEND_BOOTED) {
    renderBootMessage(event.error || event.message);
  }
});
window.addEventListener("unhandledrejection", (event) => {
  if (!window.__STORYDRIVER_FRONTEND_BOOTED) {
    renderBootMessage(event.reason || "Unhandled startup error");
  }
});

try {
  initializeUiPreset();

  ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <StoryDriverErrorBoundary>
        <App />
      </StoryDriverErrorBoundary>
    </React.StrictMode>,
  );
  window.__STORYDRIVER_FRONTEND_BOOTED = true;
} catch (error) {
  console.error("StoryDriver startup failed", error);
  renderBootMessage(error);
}
