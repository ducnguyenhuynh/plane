/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { AGENT_PROVIDERS } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import type { TAgent } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { AgentFormDialog } from "./agent-form-dialog";
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

export function AgentsSettings({ workspaceSlug }: { workspaceSlug: string }) {
  const {
    data: agents,
    mutate,
    error,
  } = useSWR(`AI_AGENTS_${workspaceSlug}`, () => agentService.fetchAgents(workspaceSlug));
  const [formAgent, setFormAgent] = useState<TAgent | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<TAgent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  const handleTest = async (agent: TAgent) => {
    setTestingId(agent.id);
    try {
      const result = await agentService.testAgent(workspaceSlug, agent.id);
      setToast({
        type: result.ok ? "success" : "error",
        title: result.ok ? "Connection works" : "Connection failed",
        message: result.message,
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: "Connection failed",
        message: getAgentErrorMessage(requestError, "Could not reach the provider."),
      });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await agentService.deleteAgent(workspaceSlug, deleteTarget.id);
      setDeleteTarget(null);
      void mutate();
      setToast({ type: "success", title: "Deleted", message: "Agent deleted." });
    } catch (requestError) {
      setToast({
        type: "error",
        title: "Could not delete the agent",
        message: getAgentErrorMessage(requestError, "Please try again."),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-end">
        <Button variant="primary" size="md" stretch="auto" label="Add agent" onClick={() => setFormAgent(null)} />
      </div>

      {!agents && !error && <p className="text-body-sm-regular text-tertiary">Loading agents…</p>}
      {error && <p className="text-body-sm-regular text-danger-primary">Could not load agents.</p>}
      {agents && agents.length === 0 && (
        <p className="text-body-sm-regular text-tertiary">
          No agents yet. Add one to connect a model you can assign to projects.
        </p>
      )}

      {agents?.map((agent) => {
        const provider = AGENT_PROVIDERS.find((item) => item.value === agent.provider);
        return (
          <div
            key={agent.id}
            className="flex flex-col gap-3 rounded-md border border-subtle p-4 md:flex-row md:items-center md:justify-between"
          >
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-body-sm-medium text-primary">{agent.name}</span>
                <span className="rounded-sm bg-layer-1 px-2 py-0.5 text-caption-sm-medium text-tertiary">
                  {provider?.label ?? agent.provider}
                </span>
                <span className="text-body-xs-regular text-tertiary">{agent.model}</span>
                {agent.reasoning_effort && (
                  <span className="text-body-xs-regular text-tertiary">effort: {agent.reasoning_effort}</span>
                )}
                {!agent.is_active && <span className="text-body-xs-regular text-danger-primary">Inactive</span>}
                {!agent.has_api_key && (
                  <span className="text-body-xs-regular text-danger-primary">Missing API key</span>
                )}
              </div>
              {agent.base_url && <span className="truncate text-body-xs-regular text-tertiary">{agent.base_url}</span>}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={testingId === agent.id ? "Testing…" : "Test"}
                loading={testingId === agent.id}
                onClick={() => void handleTest(agent)}
              />
              <Button variant="secondary" size="sm" stretch="auto" label="Edit" onClick={() => setFormAgent(agent)} />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label="Delete"
                onClick={() => setDeleteTarget(agent)}
              />
            </div>
          </div>
        );
      })}

      <AgentFormDialog
        workspaceSlug={workspaceSlug}
        agent={formAgent}
        isOpen={formAgent !== undefined}
        onClose={() => {
          setFormAgent(undefined);
          void mutate();
        }}
      />

      <ConfirmDialog
        isOpen={Boolean(deleteTarget)}
        isSubmitting={isDeleting}
        title="Delete agent"
        content={
          <>
            Delete {deleteTarget?.name}? It will be removed from every project position it holds. This action cannot be
            undone.
          </>
        }
        handleClose={() => setDeleteTarget(null)}
        handleSubmit={handleDelete}
      />
    </div>
  );
}
