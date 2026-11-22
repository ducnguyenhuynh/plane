/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// api services
import { API_BASE_URL } from "@plane/constants";
import type {
  TBackstageConnection,
  TGitHubConnection,
  TGitHubRepository,
  TProjectIntegration,
  TProjectIntegrationPayload,
} from "@plane/types";
import { APIService } from "@/services/api.service";

export class IntegrationConnectionService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async fetchGitHubConnection(workspaceSlug: string): Promise<TGitHubConnection> {
    return this.get(`/api/workspaces/${workspaceSlug}/integrations/github/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async connectGitHub(workspaceSlug: string, token: string): Promise<TGitHubConnection> {
    return this.put(`/api/workspaces/${workspaceSlug}/integrations/github/`, { token })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async disconnectGitHub(workspaceSlug: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/integrations/github/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchGitHubRepositories(workspaceSlug: string, search = ""): Promise<TGitHubRepository[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/integrations/github/repositories/`, {
      params: search ? { search } : undefined,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchBackstageConnection(workspaceSlug: string): Promise<TBackstageConnection> {
    return this.get(`/api/workspaces/${workspaceSlug}/integrations/backstage/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async connectBackstage(
    workspaceSlug: string,
    data: { base_url: string; token?: string }
  ): Promise<TBackstageConnection> {
    return this.put(`/api/workspaces/${workspaceSlug}/integrations/backstage/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async disconnectBackstage(workspaceSlug: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/integrations/backstage/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async testBackstage(workspaceSlug: string): Promise<{ ok: boolean; message: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/integrations/backstage/test/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchProjectIntegration(workspaceSlug: string, projectId: string): Promise<TProjectIntegration> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/integrations/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateProjectIntegration(
    workspaceSlug: string,
    projectId: string,
    data: TProjectIntegrationPayload
  ): Promise<TProjectIntegration> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/integrations/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async testSlack(workspaceSlug: string, projectId: string): Promise<{ ok: boolean; message: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/integrations/slack-test/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
