/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { SelectField } from "@makeplane/propel/components/select-field";
import { Switch } from "@makeplane/propel/components/switch";
import { setToast } from "@plane/blocks/toast";
import type { TProjectIntegrationPayload, TSlackEvents } from "@plane/types";
// services
import { IntegrationConnectionService } from "@/services/integrations";
// local imports
import { backstageEntityUrl, getIntegrationErrorMessage } from "./utils";

const integrationService = new IntegrationConnectionService();

const DEFAULT_EVENTS: TSlackEvents = { issue_created: true, issue_updated: false, comment_created: true };

type Props = {
  workspaceSlug: string;
  projectId: string;
  canManage: boolean;
};

export function ProjectIntegrations({ workspaceSlug, projectId, canManage }: Props) {
  const { data: integration, mutate } = useSWR(`PROJECT_INTEGRATION_${projectId}`, () =>
    integrationService.fetchProjectIntegration(workspaceSlug, projectId)
  );
  const { data: github } = useSWR(`GITHUB_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchGitHubConnection(workspaceSlug)
  );
  const { data: backstage } = useSWR(`BACKSTAGE_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchBackstageConnection(workspaceSlug)
  );
  const { data: repositories } = useSWR(github?.has_token ? `GITHUB_REPOS_${workspaceSlug}` : null, () =>
    integrationService.fetchGitHubRepositories(workspaceSlug)
  );

  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [slackEvents, setSlackEvents] = useState<TSlackEvents>(DEFAULT_EVENTS);
  const [entityRef, setEntityRef] = useState("");
  const [isBackstageEnabled, setIsBackstageEnabled] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!integration) return;
    setRepository(integration.github_repository ?? "");
    setBranch(integration.github_default_branch ?? "");
    setSlackEvents(integration.slack_events ?? DEFAULT_EVENTS);
    setEntityRef(integration.backstage_entity_ref ?? "");
    setIsBackstageEnabled(integration.is_backstage_enabled ?? false);
  }, [integration]);

  const save = async (data: TProjectIntegrationPayload, section: string, message: string) => {
    setBusy(section);
    try {
      await integrationService.updateProjectIntegration(workspaceSlug, projectId, data);
      await mutate();
      setToast({ type: "success", title: "Saved", message });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not save",
        message: getIntegrationErrorMessage(error, "Please try again."),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleTestSlack = async () => {
    setBusy("slack-test");
    try {
      const result = await integrationService.testSlack(workspaceSlug, projectId);
      setToast({
        type: result.ok ? "success" : "error",
        title: result.ok ? "Slack test sent" : "Slack test failed",
        message: result.message,
      });
    } catch (error) {
      setToast({
        type: "error",
        title: "Slack test failed",
        message: getIntegrationErrorMessage(error, "Could not reach the webhook."),
      });
    } finally {
      setBusy(null);
    }
  };

  const backstageUrl = backstageEntityUrl(backstage?.base_url ?? "", entityRef);

  return (
    <div className="flex flex-col gap-4">
      {/* GitHub */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">GitHub repository</h4>
          <p className="text-body-xs-regular text-tertiary">
            Link this project to a GitHub repository so work items and branches share one home.
          </p>
        </div>
        {!github?.has_token ? (
          <p className="text-body-xs-regular text-tertiary">
            Connect GitHub in workspace settings to pick a repository.
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <SelectField<string>
              size="lg"
              aria-label="Repository"
              label="Repository"
              value={repository}
              onValueChange={(value) => {
                if (!value) return;
                setRepository(value);
                const selected = repositories?.find((repo) => repo.full_name === value);
                if (selected) setBranch(selected.default_branch);
              }}
              options={(repositories ?? []).map((repo) => ({
                value: repo.full_name,
                label: `${repo.full_name}${repo.private ? " (private)" : ""}`,
              }))}
              placeholder={repositories ? "Select a repository" : "Loading repositories…"}
            />
            <InputField
              size="lg"
              orientation="vertical"
              name="github_branch"
              label="Default branch"
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              placeholder="main"
              autoComplete="off"
            />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canManage && github?.has_token && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "github" ? "Saving…" : "Save"}
              loading={busy === "github"}
              disabled={!repository}
              onClick={() =>
                void save(
                  {
                    github_repository: repository,
                    github_default_branch: branch,
                    is_github_enabled: true,
                  },
                  "github",
                  "Repository linked."
                )
              }
            />
          )}
          {canManage && integration?.is_github_enabled && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label="Unlink"
              onClick={() =>
                void save({ is_github_enabled: false, github_default_branch: "" }, "github", "Repository unlinked.")
              }
              disabled={busy === "github"}
            />
          )}
          {integration?.is_github_enabled && integration.github_repository_url && (
            <a
              className="text-body-xs-medium text-link-primary hover:underline"
              href={integration.github_repository_url}
              target="_blank"
              rel="noreferrer"
            >
              {integration.github_repository}
            </a>
          )}
        </div>
      </div>

      {/* Slack */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">Slack notifications</h4>
          <p className="text-body-xs-regular text-tertiary">
            Post work item activity to a Slack channel webhook. Create one in Slack under Incoming Webhooks.
          </p>
        </div>
        <InputField
          size="lg"
          orientation="vertical"
          name="slack_webhook"
          label="Webhook URL"
          type="password"
          value={webhookUrl}
          onChange={(event) => setWebhookUrl(event.target.value)}
          placeholder={
            integration?.has_slack_webhook ? "Stored webhook — leave blank to keep" : "https://hooks.slack.com/..."
          }
          autoComplete="new-password"
          disabled={!canManage}
        />
        <div className="flex flex-col gap-2">
          {[
            { key: "issue_created" as const, label: "Work item created" },
            { key: "issue_updated" as const, label: "Work item state changed" },
            { key: "comment_created" as const, label: "Comment added" },
          ].map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between gap-4">
              <span className="text-body-sm-regular text-secondary">{label}</span>
              <Switch
                size="sm"
                checked={Boolean(slackEvents[key])}
                onCheckedChange={(checked) => setSlackEvents((current) => ({ ...current, [key]: checked }))}
                aria-label={label}
                disabled={!canManage}
              />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canManage && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "slack" ? "Saving…" : "Save"}
              loading={busy === "slack"}
              onClick={() => {
                if (!webhookUrl.trim() && !integration?.has_slack_webhook) {
                  setToast({ type: "error", title: "Webhook required", message: "Paste a Slack webhook URL." });
                  return;
                }
                void save(
                  {
                    ...(webhookUrl.trim() ? { slack_webhook_url: webhookUrl.trim() } : {}),
                    slack_events: slackEvents,
                    is_slack_enabled: true,
                  },
                  "slack",
                  "Slack notifications saved."
                );
                setWebhookUrl("");
              }}
            />
          )}
          {canManage && integration?.has_slack_webhook && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={busy === "slack-test" ? "Testing…" : "Send test"}
              loading={busy === "slack-test"}
              onClick={() => void handleTestSlack()}
            />
          )}
          {canManage && integration?.is_slack_enabled && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label="Disable"
              disabled={busy === "slack"}
              onClick={() => void save({ is_slack_enabled: false }, "slack", "Slack notifications disabled.")}
            />
          )}
        </div>
      </div>

      {/* Backstage */}
      <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
        <div className="flex flex-col gap-1">
          <h4 className="text-body-sm-medium text-primary">Backstage entity</h4>
          <p className="text-body-xs-regular text-tertiary">
            Reference the catalog entity that owns this project, for example <code>component:default/my-service</code>.
          </p>
        </div>
        {!backstage?.base_url ? (
          <p className="text-body-xs-regular text-tertiary">
            Configure Backstage in workspace settings to link a catalog entity.
          </p>
        ) : (
          <InputField
            size="lg"
            orientation="vertical"
            name="backstage_entity"
            label="Entity reference"
            value={entityRef}
            onChange={(event) => setEntityRef(event.target.value)}
            placeholder="component:default/my-service"
            autoComplete="off"
            disabled={!canManage}
          />
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canManage && backstage?.base_url && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label={busy === "backstage" ? "Saving…" : "Save"}
              loading={busy === "backstage"}
              disabled={!entityRef.trim()}
              onClick={() =>
                void save(
                  { backstage_entity_ref: entityRef.trim(), is_backstage_enabled: true },
                  "backstage",
                  "Backstage entity linked."
                )
              }
            />
          )}
          {backstageUrl && isBackstageEnabled && (
            <a
              className="text-body-xs-medium text-link-primary hover:underline"
              href={backstageUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open in Backstage
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
