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
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { ProjectIntegrations } from "@/components/integration-connections";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { IntegrationsProjectSettingsHeader } from "./header";

function ProjectIntegrationsSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  // store hooks
  const { currentProjectDetails } = useProject();
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { t } = useTranslation();

  // derived values
  const canManage = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);
  const canView = allowPermissions([EUserPermissions.ADMIN, EUserPermissions.MEMBER], EUserPermissionsLevel.PROJECT);
  const pageTitle = currentProjectDetails?.name
    ? `${currentProjectDetails.name} - ${t("integrations.integrations")}`
    : undefined;

  if (workspaceUserInfo && !canView) {
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<IntegrationsProjectSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <SettingsHeading
          title={t("integrations.integrations")}
          description="Connect this project to its GitHub repository, Slack channel and Backstage catalog entity."
        />
        <div className="mt-6">
          <ProjectIntegrations workspaceSlug={workspaceSlug} projectId={projectId} canManage={canManage} />
        </div>
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(ProjectIntegrationsSettingsPage);
