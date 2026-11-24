/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// api services
import { API_BASE_URL } from "@plane/constants";
import type { TAgentRun, TAgentRunDetail, TAgentRunSession, TAgentRunSessionDetail } from "@plane/types";
import { APIService } from "@/services/api.service";

export class AgentRunService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async fetchAgentRuns(workspaceSlug: string, projectId: string, issueId: string): Promise<TAgentRun[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/agent-runs/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** The latest 50 runs of the project, across every work item. */
  async fetchProjectAgentRuns(workspaceSlug: string, projectId: string): Promise<TAgentRun[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** A follow-up run starts from the earlier sessions; without instructions it starts a fresh run. */
  async startAgentRun(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    instructions?: string
  ): Promise<TAgentRun> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/agent-runs/`,
      instructions ? { instructions } : undefined
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchAgentRun(workspaceSlug: string, projectId: string, runId: string): Promise<TAgentRunDetail> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchAgentRunSessions(workspaceSlug: string, projectId: string, runId: string): Promise<TAgentRunSession[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/sessions/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchAgentRunSession(
    workspaceSlug: string,
    projectId: string,
    runId: string,
    sessionId: string
  ): Promise<TAgentRunSessionDetail> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/sessions/${sessionId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async retryAgentRun(workspaceSlug: string, projectId: string, runId: string): Promise<TAgentRun> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/retry/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Starts a fresh OpenCode session for `sessionId`; the run keeps its history. */
  async resetAgentRunSession(
    workspaceSlug: string,
    projectId: string,
    runId: string,
    sessionId: string
  ): Promise<{ scope: string; next_session_id: string }> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/sessions/${sessionId}/reset/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** Only queued/running runs can be cancelled; the API returns the run as `failed`. */
  async cancelAgentRun(workspaceSlug: string, projectId: string, runId: string): Promise<TAgentRun> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-runs/${runId}/cancel/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
