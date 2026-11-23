/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TAgentProvider = "anthropic" | "openai" | "opencode" | "custom";

export type TAgentRole = "project_manager" | "reviewer" | "qa" | "developer";

/** Empty string means the provider/model default. */
export type TAgentReasoningEffort = "low" | "medium" | "high" | "xhigh" | "max";

/**
 * Non-admins only receive the lite shape (`id`, `name`, `provider`, `model`, `is_active`,
 * `bot_user_id`), so every admin-only field is optional.
 */
export type TAgent = {
  id: string;
  workspace_id?: string;
  name: string;
  provider: TAgentProvider;
  model: string;
  base_url?: string;
  system_prompt?: string;
  reasoning_effort?: TAgentReasoningEffort | "";
  is_active: boolean;
  /** The key itself is write-only; the API only reports whether one is stored. */
  has_api_key?: boolean;
  /** Set when the agent also exists as a bot workspace member. */
  bot_user_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type TAgentPayload = {
  name: string;
  provider: TAgentProvider;
  model: string;
  api_key?: string;
  base_url?: string;
  system_prompt?: string;
  reasoning_effort?: TAgentReasoningEffort | "";
  is_active?: boolean;
};

export type TProjectAgent = {
  id: string;
  project_id: string;
  workspace_id: string;
  agent: TAgent;
  role: TAgentRole;
  model_override: string;
  is_active: boolean;
  /** `false` when the position is benched because the role already has enough seats filled. */
  in_team: boolean;
  created_at: string;
  updated_at: string;
};

export type TProjectAgentPayload = {
  agent_id: string;
  role: TAgentRole;
  model_override?: string;
  is_active?: boolean;
};

export type TProjectAgentTeam = {
  /** `null` while the counts are still the unsaved defaults. */
  id: string | null;
  project_id: string;
  pm_count: number;
  qa_count: number;
  developer_count: number;
};
