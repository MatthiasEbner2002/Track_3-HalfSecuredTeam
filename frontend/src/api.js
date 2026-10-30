// Thin client for /api/spe. Key and applicant name travel on every call; nothing is stored in the browser.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

export function makeClient(key, applicant) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${key.trim()}`,
    "X-Applicant": encodeURIComponent(applicant.trim()), // header values are ASCII; the server decodes
  };
  async function call(path, options = {}) {
    const res = await fetch(`/api/spe${path}`, { ...options, headers });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, body.detail || `HTTP ${res.status}`);
    return body;
  }
  return {
    access: () => call("/access", { method: "POST" }),
    query: (sql) => call("/query", { method: "POST", body: JSON.stringify({ sql }) }),
    download: () => call("/download/records"),
    outputs: () => call("/outputs"),
    exportSql: (name, sql) => call("/outputs", { method: "POST", body: JSON.stringify({ name, sql }) }),
    exportText: (name, content) => call("/outputs", { method: "POST", body: JSON.stringify({ name, content }) }),
  };
}
