/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TAgentProvider, TAgentReasoningEffort, TAgentRole } from "@plane/types";

export const AGENT_PROVIDERS: { value: TAgentProvider; label: string; defaultBaseUrl: string }[] = [
  { value: "anthropic", label: "Anthropic", defaultBaseUrl: "https://api.anthropic.com" },
  { value: "openai", label: "OpenAI", defaultBaseUrl: "https://api.openai.com/v1" },
  { value: "opencode", label: "OpenCode", defaultBaseUrl: "https://opencode.ai/zen/v1" },
  { value: "custom", label: "Custom (OpenAI-compatible)", defaultBaseUrl: "" },
];

export const AGENT_ROLES: { value: TAgentRole; label: string }[] = [
  { value: "project_manager", label: "Project manager" },
  { value: "qa", label: "QA" },
  { value: "developer", label: "Developer" },
];

/**
 * Labels for every role the API can return, including `reviewer` rows created before the role was
 * retired. `AGENT_ROLES` drives the assignment options; this map keeps every row renderable.
 */
export const AGENT_ROLE_LABELS: Record<TAgentRole, string> = {
  project_manager: "Project manager",
  reviewer: "Reviewer",
  qa: "QA",
  developer: "Developer",
};

export const AGENT_EFFORT_LEVELS: { value: TAgentReasoningEffort | ""; label: string }[] = [
  { value: "", label: "Default" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
  { value: "max", label: "Max" },
];
