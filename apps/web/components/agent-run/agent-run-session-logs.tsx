/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { TAgentRunSession, TAgentRunSessionStatus, TAgentRunToolCall } from "@plane/types";
// services
import { AgentRunService } from "@/services/agent-run.service";

const agentRunService = new AgentRunService();

const POLL_INTERVAL = 5000;

type Props = {
  workspaceSlug: string;
  projectId: string;
  runId: string;
  isActive: boolean;
};

type SessionProps = Omit<Props, "isActive"> & { session: TAgentRunSession };

const statusClassName = (status: TAgentRunSessionStatus) =>
  status === "failed" ? "text-danger-primary" : status === "running" ? "text-warning-primary" : "text-tertiary";

function SessionBlock({ label, content }: { label: string; content: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-body-xs-medium text-secondary">{label}</span>
      <pre className="max-h-80 overflow-auto rounded-sm bg-layer-1 p-2 text-body-xs-regular break-words whitespace-pre-wrap">
        {content}
      </pre>
    </div>
  );
}

/** Tool calls are plain text: input is pretty-printed JSON, output and error are kept as sent. */
function ToolCalls({ toolCalls }: { toolCalls: TAgentRunToolCall[] }) {
  // translation
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-2">
      <span className="text-body-xs-medium text-secondary">{t("agent_run.session_logs.tools")}</span>
      {toolCalls.map((call, index) => {
        const input = call.input === null || call.input === undefined ? "" : JSON.stringify(call.input, null, 2);
        const failed = call.status === "failed" || call.status === "error";
        return (
          // oxlint-disable-next-line react/no-array-index-key -- tool calls carry no id; the list never reorders
          <details key={`${call.tool}-${index}`} className="rounded-sm border border-subtle p-2">
            <summary className="cursor-pointer text-body-xs-regular">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-secondary">{call.tool}</span>
                {call.title && <span className="text-tertiary">{call.title}</span>}
                {call.status && <span className={failed ? "text-danger-primary" : "text-tertiary"}>{call.status}</span>}
              </span>
            </summary>
            <div className="mt-2 flex flex-col gap-2">
              {input && <SessionBlock label={t("agent_run.session_logs.tool_input")} content={input} />}
              {call.output && <SessionBlock label={t("agent_run.session_logs.tool_output")} content={call.output} />}
              {call.error && <SessionBlock label={t("agent_run.session_logs.error")} content={call.error} />}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function SessionDetail({ workspaceSlug, projectId, runId, session }: SessionProps) {
  // translation
  const { t } = useTranslation();
  const { data } = useSWR(
    `AGENT_RUN_SESSION_${session.id}_${session.status}`,
    () => agentRunService.fetchAgentRunSession(workspaceSlug, projectId, runId, session.id),
    { refreshInterval: session.status === "running" ? POLL_INTERVAL : 0, keepPreviousData: true }
  );
  const prompt = data?.request ? JSON.stringify(data.request, null, 2) : "";

  return (
    <div className="mt-2 flex flex-col gap-2">
      {data?.session_id && (
        <p className="font-code text-caption-sm-regular break-all text-tertiary">
          {t("agent_run.session_logs.session_id")}: {data.session_id}
        </p>
      )}
      {data?.error && <SessionBlock label={t("agent_run.session_logs.error")} content={data.error} />}
      {prompt && <SessionBlock label={t("agent_run.session_logs.prompt")} content={prompt} />}
      {data?.reasoning && <SessionBlock label={t("agent_run.session_logs.reasoning")} content={data.reasoning} />}
      {data?.response && <SessionBlock label={t("agent_run.session_logs.response")} content={data.response} />}
      {data?.tool_calls && data.tool_calls.length > 0 && <ToolCalls toolCalls={data.tool_calls} />}
    </div>
  );
}

function SessionRow({ workspaceSlug, projectId, runId, session }: SessionProps) {
  // translation
  const { t } = useTranslation();
  // states
  const [isOpen, setIsOpen] = useState(false);
  const hasUsage = session.input_tokens !== null && session.output_tokens !== null;

  return (
    <details className="rounded-md border border-subtle p-3" onToggle={(event) => setIsOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-body-xs-regular">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-body-sm-medium text-primary">{session.agent_name}</span>
          <span className="text-secondary">{session.model}</span>
          <span className="text-secondary">{session.purpose}</span>
          <span className={statusClassName(session.status)}>{t(`agent_run.status.${session.status}`)}</span>
          {hasUsage && (
            <span className="text-tertiary">
              {t("agent_run.session_logs.tokens", { input: session.input_tokens, output: session.output_tokens })}
            </span>
          )}
          {session.duration_ms !== null && (
            <span className="text-tertiary">
              {t("agent_run.session_logs.duration", { seconds: Math.round(session.duration_ms / 100) / 10 })}
            </span>
          )}
          {session.stop_reason && <span className="text-tertiary">{session.stop_reason}</span>}
        </span>
      </summary>
      {isOpen && <SessionDetail workspaceSlug={workspaceSlug} projectId={projectId} runId={runId} session={session} />}
    </details>
  );
}

export function AgentRunSessionLogs({ workspaceSlug, projectId, runId, isActive }: Props) {
  // translation
  const { t } = useTranslation();
  const { data, error } = useSWR(
    `AGENT_RUN_SESSIONS_${runId}_${isActive ? "active" : "idle"}`,
    () => agentRunService.fetchAgentRunSessions(workspaceSlug, projectId, runId),
    { refreshInterval: isActive ? POLL_INTERVAL : 0, keepPreviousData: true }
  );

  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-body-sm-medium text-primary">{t("agent_run.session_logs.title")}</h4>
      {!data && !error && <p className="text-body-sm-regular text-tertiary">{t("common.loading")}</p>}
      {error && <p className="text-body-sm-regular text-tertiary">{t("agent_run.session_logs.load_failed")}</p>}
      {data?.length === 0 && <p className="text-body-sm-regular text-tertiary">{t("agent_run.session_logs.empty")}</p>}
      {data?.map((session) => (
        <SessionRow
          key={session.id}
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          runId={runId}
          session={session}
        />
      ))}
    </section>
  );
}
