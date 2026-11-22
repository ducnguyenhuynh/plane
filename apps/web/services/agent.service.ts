/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// api services
import { API_BASE_URL } from "@plane/constants";
import type { TAgent, TAgentPayload, TProjectAgent, TProjectAgentPayload, TProjectAgentTeam } from "@plane/types";
import { APIService } from "@/services/api.service";

export class AgentService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async fetchAgents(workspaceSlug: string): Promise<TAgent[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/ai-agents/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createAgent(workspaceSlug: string, data: TAgentPayload): Promise<TAgent> {
    return this.post(`/api/workspaces/${workspaceSlug}/ai-agents/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateAgent(workspaceSlug: string, agentId: string, data: Partial<TAgentPayload>): Promise<TAgent> {
    return this.patch(`/api/workspaces/${workspaceSlug}/ai-agents/${agentId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteAgent(workspaceSlug: string, agentId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/ai-agents/${agentId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async testAgent(workspaceSlug: string, agentId: string): Promise<{ ok: boolean; message: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/ai-agents/${agentId}/test/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchProjectAgents(workspaceSlug: string, projectId: string): Promise<TProjectAgent[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/ai-agents/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createProjectAgent(
    workspaceSlug: string,
    projectId: string,
    data: TProjectAgentPayload
  ): Promise<TProjectAgent> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/ai-agents/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateProjectAgent(
    workspaceSlug: string,
    projectId: string,
    assignmentId: string,
    data: Partial<TProjectAgentPayload>
  ): Promise<TProjectAgent> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/ai-agents/${assignmentId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteProjectAgent(workspaceSlug: string, projectId: string, assignmentId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/ai-agents/${assignmentId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchAgentTeam(workspaceSlug: string, projectId: string): Promise<TProjectAgentTeam> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-team/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateAgentTeam(
    workspaceSlug: string,
    projectId: string,
    data: Partial<Pick<TProjectAgentTeam, "pm_count" | "qa_count" | "developer_count">>
  ): Promise<TProjectAgentTeam> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/agent-team/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
