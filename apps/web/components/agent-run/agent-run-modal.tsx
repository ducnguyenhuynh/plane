/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { Dialog, DialogContent } from "@makeplane/propel/components/dialog";
import { setToast } from "@plane/blocks/toast";
import type { TAgentRunStage, TAgentRunStatus } from "@plane/types";
// components
import { getAgentErrorMessage } from "@/components/agents/utils";
// services
import { AgentRunService } from "@/services/agent-run.service";

const agentRunService = new AgentRunService();

const STAGES: TAgentRunStage[] = ["pm", "developer", "qa", "final"];
const STAGE_LABELS: Record<TAgentRunStage, string> = {
  pm: "Plan",
  developer: "Build",
  qa: "QA",
  final: "Final",
  done: "Done",
};

const STATUS_LABELS: Record<TAgentRunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

const STATUS_CLASSES: Record<TAgentRunStatus, string> = {
  queued: "text-tertiary",
  running: "text-warning-primary",
  completed: "text-success-primary",
  failed: "text-danger-primary",
};

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  isOpen: boolean;
  onClose: () => void;
};

export function AgentRunModal({ workspaceSlug, projectId, issueId, isOpen, onClose }: Props) {
  const listKey = `AGENT_RUNS_${issueId}`;
  const { data: runs, mutate: mutateList } = useSWR(
    isOpen ? listKey : null,
    () => agentRunService.fetchAgentRuns(workspaceSlug, projectId, issueId),
    { refreshInterval: 5000 }
  );
  const latest = runs?.[0];
  const { data: detail, mutate: mutateDetail } = useSWR(
    isOpen && latest ? `AGENT_RUN_${latest.id}` : null,
    () => agentRunService.fetchAgentRun(workspaceSlug, projectId, latest!.id),
    { refreshInterval: 5000 }
  );
  const [isStarting, setIsStarting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);

  const refresh = () => {
    void mutateList();
    void mutateDetail();
  };

  const handleStart = async () => {
    setIsStarting(true);
    try {
      await agentRunService.startAgentRun(workspaceSlug, projectId, issueId);
      refresh();
      setToast({ type: "success", title: "Agent run queued", message: "The PM is reviewing the work item." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not start the run",
        message: getAgentErrorMessage(error, "Assign agents to the project positions first."),
      });
    } finally {
      setIsStarting(false);
    }
  };

  const handleRetry = async () => {
    if (!latest) return;
    setIsRetrying(true);
    try {
      await agentRunService.retryAgentRun(workspaceSlug, projectId, latest.id);
      refresh();
      setToast({ type: "success", title: "Retry queued", message: "The run continues from its last stage." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not retry",
        message: getAgentErrorMessage(error, "Please try again."),
      });
    } finally {
      setIsRetrying(false);
    }
  };

  const active = latest && (latest.status === "queued" || latest.status === "running");

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="lg" aria-label="Agent run">
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-4 border-b border-subtle p-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-h5-medium text-primary">Agent run</h2>
              <p className="text-body-xs-regular text-tertiary">
                PM plans → developers implement → QA reviews → final result. History and artifacts are kept.
              </p>
            </div>
            <Button variant="secondary" size="md" stretch="auto" label="Close" onClick={onClose} />
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            {!runs && <p className="text-body-sm-regular text-tertiary">Loading…</p>}

            {runs && !latest && (
              <div className="flex flex-col items-start gap-3">
                <p className="text-body-sm-regular text-secondary">
                  Start a run to let the project agents plan this work item, create subtasks, implement them and review
                  the result.
                </p>
                <Button
                  variant="primary"
                  size="md"
                  stretch="auto"
                  label={isStarting ? "Starting…" : "Cook with AI"}
                  loading={isStarting}
                  onClick={() => void handleStart()}
                />
              </div>
            )}

            {latest && (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <span className={`text-body-sm-medium ${STATUS_CLASSES[latest.status]}`}>
                    {STATUS_LABELS[latest.status]}
                  </span>
                  <div className="flex items-center gap-1">
                    {STAGES.map((stage, index) => {
                      const activeIndex = STAGES.indexOf(latest.stage as TAgentRunStage);
                      const done = latest.status === "completed" || index < activeIndex;
                      return (
                        <span
                          key={stage}
                          className={`rounded-sm px-2 py-0.5 text-caption-sm-medium ${
                            done
                              ? "bg-success-subtle text-success-primary"
                              : stage === latest.stage
                                ? "bg-warning-subtle text-warning-primary"
                                : "bg-layer-1 text-tertiary"
                          }`}
                        >
                          {STAGE_LABELS[stage]}
                        </span>
                      );
                    })}
                  </div>
                  {latest.pm_agent && (
                    <span className="text-body-xs-regular text-tertiary">PM: {latest.pm_agent.name}</span>
                  )}
                  {latest.qa_agent && (
                    <span className="text-body-xs-regular text-tertiary">QA: {latest.qa_agent.name}</span>
                  )}
                </div>

                {latest.error && (
                  <div className="rounded-md border border-danger-subtle bg-danger-subtle p-3 text-body-xs-regular text-danger-primary">
                    {latest.error}
                  </div>
                )}

                {latest.status === "failed" && (
                  <div>
                    <Button
                      variant="primary"
                      size="md"
                      stretch="auto"
                      label={isRetrying ? "Retrying…" : "Retry run"}
                      loading={isRetrying}
                      onClick={() => void handleRetry()}
                    />
                  </div>
                )}

                {latest.result && (
                  <section className="flex flex-col gap-2">
                    <h4 className="text-body-sm-medium text-primary">Final result</h4>
                    <pre className="max-h-80 overflow-auto rounded-md border border-subtle p-3 text-body-xs-regular whitespace-pre-wrap text-secondary">
                      {latest.result}
                    </pre>
                  </section>
                )}

                {detail?.tasks && detail.tasks.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h4 className="text-body-sm-medium text-primary">Subtasks</h4>
                    {detail.tasks.map((task) => (
                      <div
                        key={task.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-subtle p-3"
                      >
                        <div className="flex flex-col">
                          <a
                            className="text-body-sm-medium text-link-primary hover:underline"
                            href={`/${workspaceSlug}/browse/${task.issue_identifier}`}
                          >
                            {task.issue_identifier} {task.issue_name}
                          </a>
                          <span className="text-body-xs-regular text-tertiary">
                            {task.agent?.name ?? "No agent"} · {task.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </section>
                )}

                {detail?.artifacts && detail.artifacts.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h4 className="text-body-sm-medium text-primary">Artifacts</h4>
                    {detail.artifacts.map((artifact) => (
                      <details key={artifact.id} className="rounded-md border border-subtle p-3">
                        <summary className="cursor-pointer text-body-sm-medium text-secondary">
                          {artifact.title}
                          <span className="ml-2 text-body-xs-regular text-tertiary">{artifact.kind}</span>
                        </summary>
                        <pre className="mt-2 max-h-80 overflow-auto text-body-xs-regular whitespace-pre-wrap text-tertiary">
                          {artifact.content}
                        </pre>
                      </details>
                    ))}
                  </section>
                )}

                {detail?.events && detail.events.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h4 className="text-body-sm-medium text-primary">History</h4>
                    <div className="flex flex-col gap-2">
                      {detail.events.map((event) => (
                        <div key={event.id} className="flex items-start gap-3 text-body-xs-regular">
                          <span className="w-20 shrink-0 text-tertiary">
                            {event.stage ? (STAGE_LABELS[event.stage as TAgentRunStage] ?? event.stage) : "—"}
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

                {active && (
                  <p className="text-body-xs-regular text-tertiary">
                    This run is {STATUS_LABELS[latest.status].toLowerCase()}; the panel refreshes every few seconds.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
