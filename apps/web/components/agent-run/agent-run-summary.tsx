/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { useTranslation } from "@plane/i18n";
import type { TAgentRunStage, TAgentRunStatus } from "@plane/types";
// services
import { AgentRunService } from "@/services/agent-run.service";
// local imports
import { AgentRunModal } from "./agent-run-modal";

const agentRunService = new AgentRunService();

const STAGE_LABEL_KEYS: Record<TAgentRunStage, string> = {
  pm: "agent_run.stage.pm",
  developer: "agent_run.stage.developer",
  qa: "agent_run.stage.qa",
  final: "agent_run.stage.final",
  done: "agent_run.stage.done",
};

const STATUS_CLASSES: Record<TAgentRunStatus, string> = {
  queued: "text-tertiary",
  running: "text-warning-primary",
  completed: "text-success-primary",
  failed: "text-danger-primary",
};

const isActive = (status: TAgentRunStatus | undefined) => status === "queued" || status === "running";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
};

/** Compact "AI agents" block shared by the peek overview and the work item detail sidebar. */
export function AgentRunSummary({ workspaceSlug, projectId, issueId }: Props) {
  // translation
  const { t } = useTranslation();
  // states
  const [isOpen, setIsOpen] = useState(false);
  // SWR shares this key with the modal, so opening it costs no extra request.
  const {
    data: runs,
    error,
    mutate,
  } = useSWR(`AGENT_RUNS_${issueId}`, () => agentRunService.fetchAgentRuns(workspaceSlug, projectId, issueId), {
    refreshInterval: (data) => (data?.some((run) => isActive(run.status)) ? 5000 : 0),
  });
  const latest = runs?.[0];
  const teamNames = latest
    ? [...(latest.team?.pm ?? []), ...(latest.team?.developer ?? []), ...(latest.team?.qa ?? [])]
        .map((agent) => agent.name)
        .join(", ")
    : "";

  // A 403 (guest) or a failed first request would otherwise sit on the loading state forever.
  // A failed background revalidation keeps the data we already have.
  if (error && !runs)
    return (
      <div className="flex flex-col gap-2">
        <h5 className="text-body-sm-semibold text-primary">{t("agent_run.sidebar.title")}</h5>
        <p className="text-caption-sm-regular text-danger-primary">{t("agent_run.sidebar.load_failed")}</p>
        <div>
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label={t("agent_run.sidebar.retry")}
            onClick={() => void mutate()}
          />
        </div>
      </div>
    );

  return (
    <div className="flex flex-col gap-2">
      <h5 className="text-body-sm-semibold text-primary">{t("agent_run.sidebar.title")}</h5>

      {!runs && <p className="text-caption-sm-regular text-tertiary">{t("common.loading")}</p>}
      {runs && !latest && <p className="text-caption-sm-regular text-tertiary">{t("agent_run.sidebar.no_run")}</p>}

      {latest && (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`text-caption-sm-medium ${STATUS_CLASSES[latest.status]}`}>
              {t(`agent_run.status.${latest.status}`)}
            </span>
            <span className="text-caption-sm-medium text-tertiary">
              {STAGE_LABEL_KEYS[latest.stage] ? t(STAGE_LABEL_KEYS[latest.stage]) : latest.stage}
            </span>
            {latest.rework_round > 0 && (
              <span className="text-caption-sm-regular text-tertiary">
                {t("agent_run.rework_round", { round: latest.rework_round })}
              </span>
            )}
          </div>
          {teamNames && <span className="truncate text-caption-sm-regular text-tertiary">{teamNames}</span>}
        </div>
      )}

      <div>
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          label={t("agent_run.sidebar.open_run")}
          onClick={() => setIsOpen(true)}
        />
      </div>

      <AgentRunModal
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
      />
    </div>
  );
}
