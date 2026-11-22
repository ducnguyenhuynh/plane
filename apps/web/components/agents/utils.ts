/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** DRF returns `{field: ["message"]}`; show the first one so toasts are useful. */
export function getAgentErrorMessage(error: unknown, fallback: string): string {
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
