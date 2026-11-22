/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// components
import { AgentsSettings } from "@/components/agents";
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
// local imports
import type { Route } from "./+types/page";
import { AIAgentsWorkspaceSettingsHeader } from "./header";

function AIAgentsSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { t } = useTranslation();

  // derived values
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.ai_agents.title")}`
    : undefined;

  if (workspaceUserInfo && !isAdmin) return <NotAuthorizedView section="settings" className="h-auto" />;

  return (
    <SettingsContentWrapper header={<AIAgentsWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <SettingsHeading
          title={t("workspace_settings.settings.ai_agents.heading")}
          description={t("workspace_settings.settings.ai_agents.description")}
        />
        <div className="mt-6">
          <AgentsSettings workspaceSlug={workspaceSlug} />
        </div>
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(AIAgentsSettingsPage);
