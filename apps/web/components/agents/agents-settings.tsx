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
import { useTranslation } from "@plane/i18n";
import type { TAgent } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { AgentFormDialog } from "./agent-form-dialog";
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

export function AgentsSettings({ workspaceSlug }: { workspaceSlug: string }) {
  // translation
  const { t } = useTranslation();
  // states
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
        title: result.ok
          ? t("workspace_settings.settings.ai_agents.connection_works")
          : t("workspace_settings.settings.ai_agents.connection_failed"),
        message: result.message,
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.connection_failed"),
        message: getAgentErrorMessage(
          requestError,
          t("workspace_settings.settings.ai_agents.connection_failed_message")
        ),
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
      setToast({
        type: "success",
        title: t("workspace_settings.settings.ai_agents.deleted_title"),
        message: t("workspace_settings.settings.ai_agents.deleted_message"),
      });
    } catch (requestError) {
      setToast({
        type: "error",
        title: t("workspace_settings.settings.ai_agents.delete_failed_title"),
        message: getAgentErrorMessage(requestError, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-end">
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          label={t("workspace_settings.settings.ai_agents.add_agent")}
          onClick={() => setFormAgent(null)}
        />
      </div>

      {!agents && !error && (
        <p className="text-body-sm-regular text-tertiary">{t("workspace_settings.settings.ai_agents.loading")}</p>
      )}
      {error && (
        <p className="text-body-sm-regular text-danger-primary">
          {t("workspace_settings.settings.ai_agents.load_failed")}
        </p>
      )}
      {agents && agents.length === 0 && (
        <p className="text-body-sm-regular text-tertiary">{t("workspace_settings.settings.ai_agents.empty")}</p>
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
                  <span className="text-body-xs-regular text-tertiary">
                    {t("workspace_settings.settings.ai_agents.effort", { value: agent.reasoning_effort })}
                  </span>
                )}
                {!agent.is_active && (
                  <span className="text-body-xs-regular text-danger-primary">
                    {t("workspace_settings.settings.ai_agents.inactive")}
                  </span>
                )}
                {/* Absent for non-admins: the API only reports the key to admins. */}
                {agent.has_api_key === false && (
                  <span className="text-body-xs-regular text-danger-primary">
                    {t("workspace_settings.settings.ai_agents.missing_api_key")}
                  </span>
                )}
              </div>
              {agent.base_url && <span className="truncate text-body-xs-regular text-tertiary">{agent.base_url}</span>}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={
                  testingId === agent.id
                    ? t("workspace_settings.settings.ai_agents.testing")
                    : t("workspace_settings.settings.ai_agents.test")
                }
                loading={testingId === agent.id}
                onClick={() => void handleTest(agent)}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("common.edit")}
                onClick={() => setFormAgent(agent)}
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("common.delete")}
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
        title={t("workspace_settings.settings.ai_agents.delete_title")}
        content={t("workspace_settings.settings.ai_agents.delete_content", { name: deleteTarget?.name ?? "" })}
        handleClose={() => setDeleteTarget(null)}
        handleSubmit={handleDelete}
      />
    </div>
  );
}
