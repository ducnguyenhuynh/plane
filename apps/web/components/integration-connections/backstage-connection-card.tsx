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
import { setToast } from "@plane/blocks/toast";
// services
import { IntegrationConnectionService } from "@/services/integrations";
// local imports
import { getIntegrationErrorMessage } from "./utils";

const integrationService = new IntegrationConnectionService();

export function BackstageConnectionCard({ workspaceSlug }: { workspaceSlug: string }) {
  const { data, mutate, isLoading } = useSWR(`BACKSTAGE_CONNECTION_${workspaceSlug}`, () =>
    integrationService.fetchBackstageConnection(workspaceSlug)
  );
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (data) setBaseUrl(data.base_url ?? "");
  }, [data]);

  const handleSave = async () => {
    if (!baseUrl.trim()) {
      setToast({ type: "error", title: "URL required", message: "Enter your Backstage base URL." });
      return;
    }
    setIsSaving(true);
    try {
      await integrationService.connectBackstage(workspaceSlug, {
        base_url: baseUrl.trim(),
        ...(token.trim() ? { token: token.trim() } : {}),
      });
      setToken("");
      void mutate();
      setToast({ type: "success", title: "Backstage saved", message: "Projects can now link a catalog entity." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not save Backstage",
        message: getIntegrationErrorMessage(error, "Check the URL and token."),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleTest = async () => {
    setIsTesting(true);
    try {
      const result = await integrationService.testBackstage(workspaceSlug);
      setToast({
        type: result.ok ? "success" : "error",
        title: result.ok ? "Connection works" : "Connection failed",
        message: result.message,
      });
    } catch (error) {
      setToast({
        type: "error",
        title: "Connection failed",
        message: getIntegrationErrorMessage(error, "Could not reach Backstage."),
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleDisconnect = async () => {
    setIsSaving(true);
    try {
      await integrationService.disconnectBackstage(workspaceSlug);
      setBaseUrl("");
      void mutate();
      setToast({ type: "success", title: "Disconnected", message: "Backstage links were removed from projects." });
    } catch (error) {
      setToast({
        type: "error",
        title: "Could not disconnect Backstage",
        message: getIntegrationErrorMessage(error, "Please try again."),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-md border border-subtle p-4">
      <div className="flex flex-col gap-1">
        <h4 className="text-body-sm-medium text-primary">Backstage</h4>
        <p className="text-body-xs-regular text-tertiary">
          Point Plane at your Backstage developer portal so projects can link their catalog entity and documents.
        </p>
      </div>

      {isLoading && <p className="text-body-xs-regular text-tertiary">Loading…</p>}

      <div className="grid gap-3 md:grid-cols-2">
        <InputField
          size="lg"
          orientation="vertical"
          name="backstage_url"
          label="Backstage URL"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder="https://backstage.example.com"
          autoComplete="off"
        />
        <InputField
          size="lg"
          orientation="vertical"
          name="backstage_token"
          label="Token (optional)"
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder={data?.has_token ? "Stored token — leave blank to keep" : "Backstage access token"}
          autoComplete="new-password"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          label={isSaving ? "Saving…" : "Save"}
          loading={isSaving}
          onClick={() => void handleSave()}
        />
        {data?.has_token !== undefined && (
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label={isTesting ? "Testing…" : "Test connection"}
            loading={isTesting}
            onClick={() => void handleTest()}
            disabled={!baseUrl.trim()}
          />
        )}
        {data?.base_url && (
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            label="Disconnect"
            onClick={() => void handleDisconnect()}
            disabled={isSaving}
          />
        )}
      </div>
    </div>
  );
}
