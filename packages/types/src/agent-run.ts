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

export type TAgentRunArtifact = {
  id: string;
  kind: string;
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
  output: string;
  created_at: string;
};

export type TAgentRun = {
  id: string;
  issue_id: string;
  status: TAgentRunStatus;
  stage: TAgentRunStage;
  pm_agent: TAgentRunAgent | null;
  qa_agent: TAgentRunAgent | null;
  result: string;
  error: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type TAgentRunDetail = TAgentRun & {
  events: TAgentRunEvent[];
  artifacts: TAgentRunArtifact[];
  tasks: TAgentRunTask[];
};
