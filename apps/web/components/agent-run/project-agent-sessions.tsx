/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
// plane imports
import { AGENT_ROLE_LABELS } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TAgentRun, TAgentRunSession } from "@plane/types";
// components
import { getAgentErrorMessage } from "@/components/agents/utils";
// services
import { AgentRunService } from "@/services/agent-run.service";
// local imports
import { RunMarkdown } from "./agent-run-modal";
import { SessionRow } from "./agent-run-session-logs";

const agentRunService = new AgentRunService();

const statusClassName = (status: TAgentRun["status"]) =>
  status === "failed"
    ? "text-danger-primary"
    : status === "running"
      ? "text-warning-primary"
      : status === "completed"
        ? "text-success-primary"
        : "text-tertiary";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

type RunProps = Props & { run: TAgentRun };

type TResetTarget = { rowId: string; sessionId: string };

/** Sessions that share an OpenCode session id get one reset button. */
function SessionGroup({
  workspaceSlug,
  projectId,
  runId,
  sessions,
  onReset,
}: {
  workspaceSlug: string;
  projectId: string;
  runId: string;
  sessions: TAgentRunSession[];
  onReset: (target: TResetTarget) => void;
}) {
  // translation
  const { t } = useTranslation();
  // The reset endpoint takes a session row id; any row of the same OpenCode session works
  const rowIdBySessionId = new Map<string, string>();
  for (const session of sessions) {
    if (session.session_id && !rowIdBySessionId.has(session.session_id))
      rowIdBySessionId.set(session.session_id, session.id);
  }

  return (
    <div className="flex flex-col gap-2">
      {sessions.map((session) => (
        <SessionRow
          key={session.id}
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          runId={runId}
          session={session}
        />
      ))}
      {[...rowIdBySessionId].map(([sessionId, rowId]) => (
        <div key={sessionId} className="flex flex-wrap items-center gap-2">
          <span className="font-code text-caption-sm-regular break-all text-tertiary">{sessionId}</span>
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label={t("agent_run.sessions.reset_button")}
            onClick={() => onReset({ rowId, sessionId })}
          />
        </div>
      ))}
    </div>
  );
}

function RunBody({ workspaceSlug, projectId, run }: RunProps) {
  // translation
  const { t } = useTranslation();
  // states
  const [resetTarget, setResetTarget] = useState<TResetTarget | null>(null);
  const [isResetting, setIsResetting] = useState(false);
  const { data: detail, error: detailError } = useSWR(`AGENT_PROJECT_RUN_${run.id}`, () =>
    agentRunService.fetchAgentRun(workspaceSlug, projectId, run.id)
  );
  const {
    data: sessions,
    error: sessionsError,
    mutate: mutateSessions,
  } = useSWR(
    `AGENT_PROJECT_RUN_SESSIONS_${run.id}`,
    () => agentRunService.fetchAgentRunSessions(workspaceSlug, projectId, run.id),
    { keepPreviousData: true }
  );

  const handleReset = async () => {
    if (!resetTarget) return;
    setIsResetting(true);
    try {
      await agentRunService.resetAgentRunSession(workspaceSlug, projectId, run.id, resetTarget.rowId);
      setResetTarget(null);
      await mutateSessions();
      setToast({
        type: "success",
        title: t("agent_run.sessions.reset_success_title"),
        message: t("agent_run.sessions.reset_success_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("agent_run.sessions.reset_failed_title"),
        message: getAgentErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsResetting(false);
    }
  };

  const runSessions = sessions?.filter((session) => !session.task_id) ?? [];

  return (
    <div className="mt-3 flex flex-col gap-4">
      {detailError && (
        <p className="text-body-sm-regular text-tertiary">{t("agent_run.sessions.detail_load_failed")}</p>
      )}

      {detail?.tasks && detail.tasks.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-primary">{t("agent_run.subtasks")}</h4>
          {detail.tasks.map((task) => {
            const taskSessions = sessions?.filter((session) => session.task_id === task.id) ?? [];
            return (
              <div key={task.id} className="flex flex-col gap-2 rounded-md border border-subtle p-3">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-body-xs-regular">
                  <Link
                    className="text-body-sm-medium text-link-primary hover:underline"
                    href={`/${workspaceSlug}/browse/${task.issue_identifier}`}
                  >
                    {task.issue_identifier} {task.issue_name}
                  </Link>
                  <span className="rounded-sm bg-layer-1 px-2 py-0.5 text-caption-sm-medium text-tertiary">
                    {AGENT_ROLE_LABELS[task.role]}
                  </span>
                  <span className={task.status === "blocked" ? "text-danger-primary" : "text-tertiary"}>
                    {task.status}
                  </span>
                </div>
                {task.session_id && taskSessions.length === 0 && (
                  <p className="font-code text-caption-sm-regular break-all text-tertiary">{task.session_id}</p>
                )}
                {taskSessions.length > 0 && (
                  <SessionGroup
                    workspaceSlug={workspaceSlug}
                    projectId={projectId}
                    runId={run.id}
                    sessions={taskSessions}
                    onReset={setResetTarget}
                  />
                )}
              </div>
            );
          })}
        </section>
      )}

      {sessionsError && (
        <p className="text-body-sm-regular text-tertiary">{t("agent_run.sessions.load_sessions_failed")}</p>
      )}

      {runSessions.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-primary">{t("agent_run.sessions.run_sessions")}</h4>
          <SessionGroup
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            runId={run.id}
            sessions={runSessions}
            onReset={setResetTarget}
          />
        </section>
      )}

      {detail?.artifacts && detail.artifacts.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-body-sm-medium text-primary">{t("agent_run.artifacts")}</h4>
          {detail.artifacts.map((artifact) => (
            <details key={artifact.id} className="rounded-md border border-subtle p-3">
              <summary className="cursor-pointer text-body-sm-medium text-secondary">
                {artifact.title}
                <span className="ml-2 text-body-xs-regular text-tertiary">{artifact.kind}</span>
              </summary>
              <div className="mt-2 max-h-80 overflow-auto">
                <RunMarkdown content={artifact.content} />
              </div>
            </details>
          ))}
        </section>
      )}

      <ConfirmDialog
        isOpen={Boolean(resetTarget)}
        isSubmitting={isResetting}
        title={t("agent_run.sessions.reset_confirm_title")}
        content={t("agent_run.sessions.reset_confirm_content", { session: resetTarget?.sessionId ?? "" })}
        primaryButtonText={{
          loading: t("agent_run.sessions.resetting"),
          default: t("agent_run.sessions.reset_button"),
        }}
        secondaryButtonText={t("cancel")}
        handleClose={() => setResetTarget(null)}
        handleSubmit={handleReset}
      />
    </div>
  );
}

function RunRow({ workspaceSlug, projectId, run }: RunProps) {
  // translation
  const { t } = useTranslation();
  // states
  const [isOpen, setIsOpen] = useState(false);

  return (
    <details className="rounded-md border border-subtle p-3" onToggle={(event) => setIsOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-body-xs-regular">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-body-sm-medium text-primary">{run.issue_identifier}</span>
          <span className="text-secondary">{run.issue_name}</span>
          <span className={statusClassName(run.status)}>{t(`agent_run.status.${run.status}`)}</span>
          <span className="text-tertiary">{new Date(run.created_at).toLocaleString()}</span>
        </span>
      </summary>
      {isOpen && <RunBody workspaceSlug={workspaceSlug} projectId={projectId} run={run} />}
    </details>
  );
}

export function ProjectAgentSessions({ workspaceSlug, projectId }: Props) {
  // translation
  const { t } = useTranslation();
  const { data: runs, error } = useSWR(`AGENT_PROJECT_RUNS_${projectId}`, () =>
    agentRunService.fetchProjectAgentRuns(workspaceSlug, projectId)
  );

  return (
    <section className="flex flex-col gap-2">
      {!runs && !error && <p className="text-body-sm-regular text-tertiary">{t("common.loading")}</p>}
      {error && <p className="text-body-sm-regular text-tertiary">{t("agent_run.sessions.load_failed")}</p>}
      {runs?.length === 0 && <p className="text-body-sm-regular text-tertiary">{t("agent_run.sessions.empty")}</p>}
      {runs?.map((run) => (
        <RunRow key={run.id} workspaceSlug={workspaceSlug} projectId={projectId} run={run} />
      ))}
    </section>
  );
}
