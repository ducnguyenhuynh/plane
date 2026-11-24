/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TAgentRunStatus = "queued" | "running" | "completed" | "failed";
export type TAgentRunStage = "pm" | "developer" | "qa" | "final" | "done";

export type TAgentRunAgent = {
  id: string;
  name: string;
  provider: string;
  model: string;
  bot_user_id: string | null;
};

/** The agents picked for the run, grouped by the role they play. */
export type TAgentRunTeam = {
  pm: TAgentRunAgent[];
  developer: TAgentRunAgent[];
  qa: TAgentRunAgent[];
};

export type TAgentRunEvent = {
  id: string;
  stage: string;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  agent: TAgentRunAgent | null;
  created_at: string;
};

export type TAgentRunArtifactKind = "plan" | "plan_candidate" | "subtask" | "review" | "final";

export type TAgentRunArtifact = {
  id: string;
  kind: TAgentRunArtifactKind | string;
  title: string;
  content: string;
  data: Record<string, unknown>;
  created_at: string;
};

export type TAgentRunTask = {
  id: string;
  issue_id: string;
  issue_identifier: string;
  issue_name: string;
  agent: TAgentRunAgent | null;
  status: string;
  paths: string[];
  depends_on: string[];
  commit_sha: string;
  merged_sha: string;
  changed_paths: string[];
  conflict_attempts: number;
  created_at: string;
};

export type TAgentRunSessionStatus = "running" | "completed" | "failed";

/** One tool call the model made inside a session. */
export type TAgentRunToolCall = {
  tool: string;
  status: string;
  title: string;
  input: unknown;
  output: string;
  error: string;
};

/** One call to an AI model made during a run. */
export type TAgentRunSession = {
  id: string;
  run_id: string;
  task_id: string;
  agent_id: string;
  agent_name: string;
  stage: string;
  purpose: string;
  session_id: string;
  rework_round: number;
  provider: string;
  model: string;
  effort: string;
  status: TAgentRunSessionStatus;
  stop_reason: string;
  max_tokens: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  /** As a decimal string, because it is a DecimalField on the API. */
  cost: string | null;
  error: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  created_at: string;
};

export type TAgentRunSessionDetail = TAgentRunSession & {
  /** The exact JSON sent to the provider. */
  request: Record<string, unknown>;
  response: string;
  reasoning: string;
  usage: Record<string, unknown>;
  tool_calls: TAgentRunToolCall[];
};

export type TAgentRun = {
  id: string;
  issue_id: string;
  status: TAgentRunStatus;
  stage: TAgentRunStage;
  pm_agent: TAgentRunAgent | null;
  qa_agent: TAgentRunAgent | null;
  team: TAgentRunTeam;
  /** 1 on the first pass; grows when QA sends the work back. */
  rework_round: number;
  result: string;
  error: string;
  parent_run_id: string | null;
  instructions: string;
  repository: string;
  base_branch: string;
  branch: string;
  base_sha: string;
  head_sha: string;
  pull_request_number: number | null;
  pull_request_url: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type TAgentRunDetail = TAgentRun & {
  events: TAgentRunEvent[];
  artifacts: TAgentRunArtifact[];
  tasks: TAgentRunTask[];
};
