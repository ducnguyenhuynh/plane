/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { InputField } from "@makeplane/propel/components/input-field";
import { setToast } from "@plane/blocks/toast";
// services
import { IntegrationConnectionService } from "@/services/integrations";
// local imports
import { getIntegrationErrorMessage } from "./utils";

const integrationService = new IntegrationConnectionService();

export function GitHubConnectionCard({ workspaceSlug }: { workspaceSlug: string }) {
  const { data, mutate, isLoading } = useSWR(`GITHUB_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchGitHubConnection(workspaceSlug)
  );
  const [token, setToken] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const handleConnect = async () => {
    if (!token.trim()) {
      setToast({ type: "error", title: "Token required", message: "Paste a GitHub personal access token." });
      return;
    }
    setIsSaving(true);
    try {
      await integrationService.connectGitHub(workspaceSlug, token.trim());
      setToken("");
      void mutate();
      setToast({ type: "success", title: "GitHub connected", message: "Repositories can now be linked to projects." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not connect GitHub",
        message: getIntegrationErrorMessage(error, "Check the token and try again."),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setIsSaving(true);
    try {
      await integrationService.disconnectGitHub(workspaceSlug);
      void mutate();
      setToast({ type: "success", title: "Disconnected", message: "GitHub repositories were unlinked from projects." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not disconnect GitHub",
        message: getIntegrationErrorMessage(error, "Please try again."),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
      <div className="flex flex-col gap-1">
        <h4 className="text-body-sm-medium text-primary">GitHub</h4>
        <p className="text-body-xs-regular text-tertiary">
          Connect with a personal access token (classic) that has the <code>repo</code> scope to link repositories to
          projects.
        </p>
      </div>

      {isLoading && <p className="text-body-xs-regular text-tertiary">Loading…</p>}

      {data?.has_token ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-body-sm-regular text-secondary">
            Connected as <span className="text-primary">@{data.login}</span>
          </span>
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label={isSaving ? "Disconnecting…" : "Disconnect"}
            loading={isSaving}
            onClick={() => void handleDisconnect()}
          />
        </div>
      ) : (
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <div className="grow">
            <InputField
              size="lg"
              orientation="vertical"
              name="github_token"
              label="Personal access token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="ghp_..."
              autoComplete="new-password"
            />
          </div>
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            label={isSaving ? "Connecting…" : "Connect"}
            loading={isSaving}
            onClick={() => void handleConnect()}
          />
        </div>
      )}
    </div>
  );
}
