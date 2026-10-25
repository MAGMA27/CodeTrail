import { useState } from "react";

const apiBaseUrl = "";

function requestJson(path: string, validator: (value: unknown) => boolean, init: RequestInit = {}) {
  const { timeoutMs, ...requestInit } = init;
  void timeoutMs;
  void validator;
  return fetch(`${apiBaseUrl}${path}`, { ...requestInit });
}

export async function refreshPreferences() {
  await requestJson("/api/preferences", (value) => Boolean(value), { method: "POST" });
  await requestJson("/api/preferences/audit", (value) => Boolean(value), { method: "POST" });
}

export function PreferencesPage() {
  const [saved, setSaved] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  function savePreferences() {
    setSaved(true);
  }

  function openHelp() {
    window.location.assign("/help");
  }

  return (
    <main>
      <form onSubmit={savePreferences}>
        <button type="submit">Save preferences</button>
        <span>{saved ? "Saved" : "Unsaved"}</span>
      </form>
      <button onClick={() => setPanelOpen(!panelOpen)}>Toggle local panel</button>
      <span>{panelOpen ? "Open" : "Closed"}</span>
      <button onClick={refreshPreferences}>Refresh preferences</button>
      <button onClick={openHelp}>Open help</button>
    </main>
  );
}
