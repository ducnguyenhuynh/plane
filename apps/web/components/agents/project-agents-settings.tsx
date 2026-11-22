/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
// plane imports
import { AGENT_ROLES } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import type { TProjectAgent } from "@plane/types";
// services
import { AgentService } from "@/services/agent.service";
// local imports
import { AssignAgentDialog } from "./assign-agent-dialog";
import { getAgentErrorMessage } from "./utils";

const agentService = new AgentService();

type Props = {
  workspaceSlug: string;
  projectId: string;
  canManage: boolean;
};

export function ProjectAgentsSettings({ workspaceSlug, projectId, canManage }: Props) {
  const {
    data: assignments,
    mutate,
    error,
  } = useSWR(`PROJECT_AI_AGENTS_${projectId}`, () => agentService.fetchProjectAgents(workspaceSlug, projectId));
  const { data: agents } = useSWR(`AI_AGENTS_${workspaceSlug}`, () => agentService.fetchAgents(workspaceSlug));
  const { data: team, mutate: mutateTeam } = useSWR(`PROJECT_AGENT_TEAM_${projectId}`, () =>
    agentService.fetchAgentTeam(workspaceSlug, projectId)
  );
  const [formAssignment, setFormAssignment] = useState<TProjectAgent | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<TProjectAgent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [teamCounts, setTeamCounts] = useState({ pm_count: "1", qa_count: "1", developer_count: "3" });
  const [isSavingTeam, setIsSavingTeam] = useState(false);

  useEffect(() => {
    if (!team) return;
    setTeamCounts({
      pm_count: String(team.pm_count),
      qa_count: String(team.qa_count),
      developer_count: String(team.developer_count),
    });
  }, [team]);

  const saveTeam = async () => {
    const counts = {
      pm_count: Number(teamCounts.pm_count),
      qa_count: Number(teamCounts.qa_count),
      developer_count: Number(teamCounts.developer_count),
    };
    if (Object.values(counts).some((value) => !Number.isInteger(value) || value < 1 || value > 10)) {
      setToast({ type: "error", title: "Invalid team size", message: "Choose a number between 1 and 10." });
      return;
    }
    setIsSavingTeam(true);
    try {
      await agentService.updateAgentTeam(workspaceSlug, projectId, counts);
      await mutateTeam();
      setToast({ type: "success", title: "Saved", message: "Team size updated for future runs." });
    } catch (requestError) {
      setToast({
        type: "error",
        title: "Could not save team size",
        message: getAgentErrorMessage(requestError, "Please try again."),
      });
    } finally {
      setIsSavingTeam(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await agentService.deleteProjectAgent(workspaceSlug, projectId, deleteTarget.id);
      setDeleteTarget(null);
      void mutate();
      setToast({ type: "success", title: "Removed", message: "Agent removed from the project." });
    } catch (requestError) {
      setToast({
        type: "error",
        title: "Could not remove the agent",
        message: getAgentErrorMessage(requestError, "Please try again."),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">Team size</h4>
          <p className="text-body-xs-regular text-tertiary">
            How many agents of each role can work in parallel. PM planning uses up to the manager count; developers and
            QA run in parallel up to these limits. Changes apply to future runs.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <InputField
            size="lg"
            orientation="vertical"
            name="pm_count"
            label="PM / Tech lead"
            type="number"
            value={teamCounts.pm_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, pm_count: event.target.value }))}
            disabled={!canManage}
          />
          <InputField
            size="lg"
            orientation="vertical"
            name="qa_count"
            label="QA"
            type="number"
            value={teamCounts.qa_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, qa_count: event.target.value }))}
            disabled={!canManage}
          />
          <InputField
            size="lg"
            orientation="vertical"
            name="developer_count"
            label="Developers"
            type="number"
            value={teamCounts.developer_count}
            onChange={(event) => setTeamCounts((current) => ({ ...current, developer_count: event.target.value }))}
            disabled={!canManage}
          />
        </div>
        {canManage && (
          <div>
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={isSavingTeam ? "Saving…" : "Save team size"}
              loading={isSavingTeam}
              onClick={() => void saveTeam()}
            />
          </div>
        )}
      </div>

      {canManage && (
        <div className="flex items-center justify-end">
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            label="Assign agent"
            onClick={() => setFormAssignment(null)}
          />
        </div>
      )}

      {!assignments && !error && <p className="text-body-sm-regular text-tertiary">Loading positions…</p>}
      {error && <p className="text-body-sm-regular text-danger-primary">Could not load project positions.</p>}
      {assignments && assignments.length === 0 && (
        <p className="text-body-sm-regular text-tertiary">
          No agents are assigned to this project yet. Agents are defined once in workspace settings, then placed in a
          position here.
        </p>
      )}

      {AGENT_ROLES.map(({ value: role, label }) => {
        const roleAssignments = assignments?.filter((assignment) => assignment.role === role) ?? [];
        if (!roleAssignments.length) return null;
        return (
          <section key={role} className="flex flex-col gap-2">
            <h4 className="text-body-sm-medium text-secondary">{label}</h4>
            {roleAssignments.map((assignment) => (
              <div
                key={assignment.id}
                className="flex flex-col gap-3 rounded-md border border-subtle p-4 md:flex-row md:items-center md:justify-between"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-body-sm-medium text-primary">{assignment.agent.name}</span>
                    <span className="text-body-xs-regular text-tertiary">
                      {assignment.model_override || assignment.agent.model}
                      {assignment.model_override ? " (override)" : ""}
                    </span>
                    {!assignment.is_active && (
                      <span className="text-body-xs-regular text-danger-primary">Inactive</span>
                    )}
                  </div>
                </div>
                {canManage && (
                  <div className="flex shrink-0 items-center gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      stretch="auto"
                      label="Edit"
                      onClick={() => setFormAssignment(assignment)}
                    />
                    <Button
                      variant="secondary"
                      size="sm"
                      stretch="auto"
                      label="Remove"
                      onClick={() => setDeleteTarget(assignment)}
                    />
                  </div>
                )}
              </div>
            ))}
          </section>
        );
      })}

      {canManage && (
        <AssignAgentDialog
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          agents={agents ?? []}
          assignment={formAssignment}
          isOpen={formAssignment !== undefined}
          onClose={() => {
            setFormAssignment(undefined);
            void mutate();
          }}
        />
      )}

      <ConfirmDialog
        isOpen={Boolean(deleteTarget)}
        isSubmitting={isDeleting}
        title="Remove agent from project"
        content={
          <>
            Remove {deleteTarget?.agent.name} from the {deleteTarget?.role.replace("_", " ")} position? The workspace
            agent itself is not deleted.
          </>
        }
        handleClose={() => setDeleteTarget(null)}
        handleSubmit={handleDelete}
      />
    </div>
  );
}
