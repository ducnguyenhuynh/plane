/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** DRF returns `{field: ["message"]}`; show the first one so toasts are useful. */
export function getIntegrationErrorMessage(error: unknown, fallback: string): string {
  if (!error) return fallback;
  if (typeof error === "string") return error;
  if (typeof error === "object") {
    for (const value of Object.values(error as Record<string, unknown>)) {
      if (Array.isArray(value) && value.length) return String(value[0]);
      if (typeof value === "string") return value;
    }
  }
  return fallback;
}

/** Backstage entity refs look like `component:default/my-service` or `default/my-service`. */
export function backstageEntityUrl(baseUrl: string, entityRef: string): string {
  if (!baseUrl || !entityRef) return "";
  let kind = "component";
  let namespace = "default";
  let rest = entityRef.trim();
  if (rest.includes(":")) {
    const [refKind, refRest] = rest.split(":", 2);
    kind = refKind || kind;
    rest = refRest;
  }
  if (rest.includes("/")) {
    const parts = rest.split("/");
    namespace = parts[0] || namespace;
    rest = parts.slice(1).join("/");
  }
  return `${baseUrl.replace(/\/$/, "")}/catalog/${namespace}/${kind}/${rest}`;
}
