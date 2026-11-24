/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { Dialog, DialogContent } from "@makeplane/propel/components/dialog";
import { SelectField } from "@makeplane/propel/components/select-field";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TAgentRunStage, TAgentRunStatus } from "@plane/types";
// components
import { getAgentErrorMessage } from "@/components/agents/utils";
// local imports
import { AgentRunGitSummary, commitUrlForRun } from "./agent-run-git";
import { AgentRunSessionLogs } from "./agent-run-session-logs";
// services
import { AgentRunService } from "@/services/agent-run.service";

const agentRunService = new AgentRunService();

const STAGES: TAgentRunStage[] = ["pm", "developer", "qa", "final"];

const isActiveRun = (run: { status: TAgentRunStatus } | undefined) =>
  run?.status === "queued" || run?.status === "running";

const POLL_INTERVAL = 5000;

/** Runs are markdown; code blocks get their own horizontal scroll so they never widen the dialog. */
function RunMarkdown({ content }: { content: string }) {
  return (
    <div className="flex flex-col gap-2 text-body-xs-regular text-secondary">
      <ReactMarkdown
        components={{
          h1: ({ children }) => <h3 className="text-body-sm-semibold text-primary">{children}</h3>,
          h2: ({ children }) => <h3 className="text-body-sm-semibold text-primary">{children}</h3>,
          h3: ({ children }) => <h4 className="text-body-xs-semibold text-primary">{children}</h4>,
          p: ({ children }) => <p className="text-body-xs-regular text-secondary">{children}</p>,
          ul: ({ children }) => <ul className="ml-5 list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="ml-5 list-decimal">{children}</ol>,
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-sm bg-layer-1 p-2 text-body-xs-regular">{children}</pre>
          ),
          code: ({ children }) => <code className="text-body-xs-regular">{children}</code>,
          a: ({ href, children }) => (
            <a className="text-link-primary hover:underline" href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  isOpen: boolean;
  onClose: () => void;
};

export function AgentRunModal({ workspaceSlug, projectId, issueId, isOpen, onClose }: Props) {
  // translation
  const { t } = useTranslation();
  // states
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isCancelOpen, setIsCancelOpen] = useState(false);
  const [followUp, setFollowUp] = useState("");
  // derived keys
  const listKey = `AGENT_RUNS_${issueId}`;
  const { data: runs, mutate: mutateList } = useSWR(
    isOpen ? listKey : null,
    () => agentRunService.fetchAgentRuns(workspaceSlug, projectId, issueId),
    // Only poll while a run is in flight; a finished issue costs nothing.
    { refreshInterval: (data) => (data?.some(isActiveRun) ? POLL_INTERVAL : 0) }
  );
  const latest = runs?.[0];
  const selected = (selectedRunId ? runs?.find((run) => run.id === selectedRunId) : undefined) ?? latest;
  const { data: detail, mutate: mutateDetail } = useSWR(
    isOpen && selected ? `AGENT_RUN_${selected.id}` : null,
    () => agentRunService.fetchAgentRun(workspaceSlug, projectId, selected!.id),
    { refreshInterval: (data) => (isActiveRun(data) ? POLL_INTERVAL : 0) }
  );

  const statusLabel = (status: TAgentRunStatus) => t(`agent_run.status.${status}`);
  const stageLabel = (stage: string) =>
    STAGES.includes(stage as TAgentRunStage) || stage === "done" ? t(`agent_run.stage.${stage}`) : stage;

  const refresh = () => {
    void mutateList();
    void mutateDetail();
  };

  const handleStart = async (instructions?: string) => {
    setIsStarting(true);
    try {
      await agentRunService.startAgentRun(workspaceSlug, projectId, issueId, instructions);
      setFollowUp("");
      setSelectedRunId(null);
      refresh();
      setToast({
        type: "success",
        title: t("agent_run.toast.started_title"),
        message: t("agent_run.toast.started_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("agent_run.toast.start_failed_title"),
        message: getAgentErrorMessage(error, t("agent_run.toast.start_failed_message")),
      });
    } finally {
      setIsStarting(false);
    }
  };

  const handleRetry = async () => {
    if (!selected) return;
    setIsRetrying(true);
    try {
      await agentRunService.retryAgentRun(workspaceSlug, projectId, selected.id);
      refresh();
      setToast({
        type: "success",
        title: t("agent_run.toast.retry_title"),
        message: t("agent_run.toast.retry_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("agent_run.toast.retry_failed_title"),
        message: getAgentErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsRetrying(false);
    }
  };

  const handleCancel = async () => {
    if (!selected) return;
    setIsCancelling(true);
    try {
      await agentRunService.cancelAgentRun(workspaceSlug, projectId, selected.id);
      setIsCancelOpen(false);
      refresh();
      setToast({
        type: "success",
        title: t("agent_run.toast.cancelled_title"),
        message: t("agent_run.toast.cancelled_message"),
      });
    } catch (error) {
      setToast({
        type: "error",
        title: t("agent_run.toast.cancel_failed_title"),
        message: getAgentErrorMessage(error, t("agent_run.toast.try_again")),
      });
    } finally {
      setIsCancelling(false);
    }
  };

  // A new run is only accepted once the latest one finished.
  const canStartNew = !latest || latest.status === "completed" || latest.status === "failed";
  const selectedIsActive = isActiveRun(selected);
  const teamRows = selected
    ? [
        { key: "pm", labelKey: "agent_run.team_pm", names: selected.team?.pm?.map((agent) => agent.name) ?? [] },
        {
          key: "developer",
          labelKey: "agent_run.team_developers",
          names: selected.team?.developer?.map((agent) => agent.name) ?? [],
        },
        { key: "qa", labelKey: "agent_run.team_qa", names: selected.team?.qa?.map((agent) => agent.name) ?? [] },
      ].filter((row) => row.names.length > 0)
    : [];

  return (
    <>
      <Dialog
        open={isOpen}
        onOpenChange={(open) => {
          if (!open) {
            setIsCancelOpen(false);
            onClose();
          }
        }}
      >
        <DialogContent size="lg" aria-label={t("agent_run.title")}>
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between gap-4 border-b border-subtle p-4">
              <div className="flex flex-col gap-1">
                <h2 className="text-h5-medium text-primary">{t("agent_run.title")}</h2>
                <p className="text-body-xs-regular text-tertiary">{t("agent_run.subtitle")}</p>
              </div>
              <Button variant="secondary" size="md" stretch="auto" label={t("close")} onClick={onClose} />
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
              {!runs && <p className="text-body-sm-regular text-tertiary">{t("common.loading")}</p>}

              {runs && !latest && (
                <div className="flex flex-col items-start gap-3">
                  <p className="text-body-sm-regular text-secondary">{t("agent_run.empty_hint")}</p>
                  <Button
                    variant="primary"
                    size="md"
                    stretch="auto"
                    label={isStarting ? t("agent_run.starting") : t("agent_run.cook_with_ai")}
                    loading={isStarting}
                    onClick={() => void handleStart()}
                  />
                </div>
              )}

              {runs && runs.length > 1 && selected && (
                <SelectField<string>
                  size="lg"
                  aria-label={t("agent_run.run_history")}
                  label={t("agent_run.run_history")}
                  value={selected.id}
                  onValueChange={(value) => value && setSelectedRunId(value)}
                  options={runs.map((run) => ({
                    value: run.id,
                    label: t("agent_run.run_option", {
                      status: statusLabel(run.status),
                      date: new Date(run.created_at).toLocaleString(),
                    }),
                  }))}
                />
              )}

              {selected && (
                <>
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      className={`text-body-sm-medium ${
                        selected.status === "queued"
                          ? "text-tertiary"
                          : selected.status === "running"
                            ? "text-warning-primary"
                            : selected.status === "completed"
                              ? "text-success-primary"
                              : "text-danger-primary"
                      }`}
                    >
                      {statusLabel(selected.status)}
                    </span>
                    <div className="flex items-center gap-1">
                      {STAGES.map((stage, index) => {
                        const activeIndex = STAGES.indexOf(selected.stage as TAgentRunStage);
                        const done = selected.status === "completed" || index < activeIndex;
                        const failedHere = selected.status === "failed" && stage === selected.stage;
                        return (
                          <span
                            key={stage}
                            className={`rounded-sm px-2 py-0.5 text-caption-sm-medium ${
                              done
                                ? "bg-success-subtle text-success-primary"
                                : failedHere
                                  ? "bg-danger-subtle text-danger-primary"
                                  : stage === selected.stage
                                    ? "bg-warning-subtle text-warning-primary"
                                    : "bg-layer-1 text-tertiary"
                            }`}
                          >
                            {stageLabel(stage)}
                          </span>
                        );
                      })}
                    </div>
                    {selected.rework_round > 0 && (
                      <span className="text-body-xs-regular text-tertiary">
                        {t("agent_run.rework_round", { round: selected.rework_round })}
                      </span>
                    )}
                  </div>

                  {teamRows.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      {teamRows.map((row) => (
                        <span key={row.key} className="text-body-xs-regular text-tertiary">
                          {t(row.labelKey, { names: row.names.join(", ") })}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-2">
                    {canStartNew && (
                      <Button
                        variant="primary"
                        size="md"
                        stretch="auto"
                        label={isStarting ? t("agent_run.starting") : t("agent_run.start_new_run")}
                        loading={isStarting}
                        onClick={() => void handleStart()}
                      />
                    )}
                    {selected.status === "failed" && (
                      <Button
                        variant="secondary"
                        size="md"
                        stretch="auto"
                        label={isRetrying ? t("agent_run.retrying") : t("agent_run.retry_run")}
                        loading={isRetrying}
                        onClick={() => void handleRetry()}
                      />
                    )}
                    {selectedIsActive && (
                      <Button
                        variant="secondary"
                        size="md"
                        stretch="auto"
                        label={t("agent_run.cancel_run")}
                        onClick={() => setIsCancelOpen(true)}
                      />
                    )}
                  </div>

                  {canStartNew && (
                    <section className="flex flex-col gap-2">
                      <h4 className="text-body-sm-medium text-primary">{t("agent_run.follow_up.title")}</h4>
                      <label className="text-body-xs-medium text-secondary" htmlFor="agent-run-follow-up">
                        {t("agent_run.follow_up.label")}
                      </label>
                      <textarea
                        id="agent-run-follow-up"
                        rows={3}
                        value={followUp}
                        onChange={(event) => setFollowUp(event.target.value)}
                        placeholder={t("agent_run.follow_up.placeholder")}
                        className="w-full rounded-md border border-subtle bg-layer-1 p-2 text-body-sm-regular text-primary placeholder:text-tertiary focus-visible:ring-2 focus-visible:ring-accent-strong focus-visible:outline-none"
                      />
                      <p className="text-body-xs-regular text-tertiary">{t("agent_run.follow_up.hint")}</p>
                      <div>
                        <Button
                          variant="primary"
                          size="md"
                          stretch="auto"
                          label={isStarting ? t("agent_run.follow_up.submitting") : t("agent_run.follow_up.submit")}
                          loading={isStarting}
                          disabled={!followUp.trim() || isStarting}
                          onClick={() => void handleStart(followUp.trim())}
                        />
                      </div>
                    </section>
                  )}

                  {selected.error && (
                    <div className="rounded-md border border-danger-subtle bg-danger-subtle p-3 text-body-xs-regular text-danger-primary">
                      {selected.error}
                    </div>
                  )}

                  <AgentRunGitSummary run={detail ?? selected} />

                  {selected.result && (
                    <section className="flex flex-col gap-2">
                      <h4 className="text-body-sm-medium text-primary">{t("agent_run.final_result")}</h4>
                      <div className="max-h-80 overflow-auto rounded-md border border-subtle p-3">
                        <RunMarkdown content={selected.result} />
                      </div>
                    </section>
                  )}

                  {detail?.tasks && detail.tasks.length > 0 && (
                    <section className="flex flex-col gap-2">
                      <h4 className="text-body-sm-medium text-primary">{t("agent_run.subtasks")}</h4>
                      {detail.tasks.map((task) => {
                        const sha = task.merged_sha || task.commit_sha;
                        const commitUrl = commitUrlForRun(detail ?? selected, detail.repository, sha);
                        return (
                          <div
                            key={task.id}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-subtle p-3"
                          >
                            <div className="flex flex-col">
                              <Link
                                className="text-body-sm-medium text-link-primary hover:underline"
                                href={`/${workspaceSlug}/browse/${task.issue_identifier}`}
                              >
                                {task.issue_identifier} {task.issue_name}
                              </Link>
                              <span
                                className={`text-body-xs-regular ${
                                  task.status === "blocked" ? "text-danger-primary" : "text-tertiary"
                                }`}
                              >
                                {task.agent?.name ?? t("agent_run.no_agent")} · {task.status}
                              </span>
                              <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-body-xs-regular text-tertiary">
                                {sha &&
                                  (commitUrl ? (
                                    <a
                                      className="text-link-primary hover:underline"
                                      href={commitUrl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                    >
                                      {sha.slice(0, 7)}
                                    </a>
                                  ) : (
                                    <span>{sha.slice(0, 7)}</span>
                                  ))}
                                {task.changed_paths.length > 0 && (
                                  <span>{t("agent_run.git.files_changed", { count: task.changed_paths.length })}</span>
                                )}
                                {task.conflict_attempts > 0 && (
                                  <span>{t("agent_run.git.conflict_attempts", { count: task.conflict_attempts })}</span>
                                )}
                              </span>
                            </div>
                          </div>
                        );
                      })}
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

                  <AgentRunSessionLogs
                    key={selected.id}
                    workspaceSlug={workspaceSlug}
                    projectId={projectId}
                    runId={selected.id}
                    isActive={selectedIsActive}
                  />

                  {detail?.events && detail.events.length > 0 && (
                    <section className="flex flex-col gap-2">
                      <h4 className="text-body-sm-medium text-primary">{t("agent_run.history")}</h4>
                      <div className="flex flex-col gap-2">
                        {detail.events.map((event) => (
                          <div key={event.id} className="flex items-start gap-3 text-body-xs-regular">
                            <span className="w-20 shrink-0 text-tertiary">
                              {event.stage ? stageLabel(event.stage) : "—"}
                            </span>
                            <span className={event.kind === "error" ? "text-danger-primary" : "text-secondary"}>
                              {event.message}
                            </span>
                            <span className="ml-auto shrink-0 text-tertiary">
                              {new Date(event.created_at).toLocaleTimeString()}
                            </span>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {selectedIsActive && (
                    <p className="text-body-xs-regular text-tertiary">
                      {t("agent_run.active_hint", { status: statusLabel(selected.status).toLowerCase() })}
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        isOpen={isCancelOpen}
        isSubmitting={isCancelling}
        title={t("agent_run.cancel_title")}
        content={t("agent_run.cancel_content")}
        variant="warning"
        primaryButtonText={{ loading: t("agent_run.cancelling"), default: t("agent_run.cancel_run") }}
        secondaryButtonText={t("close")}
        handleClose={() => setIsCancelOpen(false)}
        handleSubmit={handleCancel}
      />
    </>
  );
}
