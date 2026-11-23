/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { APP_INTEGRATIONS, EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { WorkspaceIntegrationConnections } from "@/components/integration-connections";
import { SingleIntegrationCard } from "@/components/integration/single-integration-card";
import { IntegrationsSettingsLoader } from "@/components/ui/loader/settings/integration";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
// services
import { IntegrationService } from "@/services/integrations";
// local imports
import type { Route } from "./+types/page";

const integrationService = new IntegrationService();

function WorkspaceIntegrationsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  // translation
  const { t } = useTranslation();
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();

  // derived values
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const pageTitle = currentWorkspace?.name ? `${currentWorkspace.name} - ${t("integrations.integrations")}` : undefined;
  const { data: appIntegrations } = useSWR(isAdmin ? APP_INTEGRATIONS : null, () =>
    isAdmin ? integrationService.getAppIntegrationsList() : null
  );

  if (workspaceUserInfo && !isAdmin) return <NotAuthorizedView section="settings" className="h-auto" />;

  return (
    <>
      <PageHead title={pageTitle} />
      <section className="w-full overflow-y-auto">
        <div className="flex items-start gap-3 border-b border-subtle py-3.5">
          <h3 className="text-h5-medium">{t("integrations.integrations")}</h3>
        </div>
        <WorkspaceIntegrationConnections workspaceSlug={workspaceSlug} />
        <div>
          {appIntegrations ? (
            appIntegrations.map((integration) => (
              <SingleIntegrationCard key={integration.id} integration={integration} />
            ))
          ) : (
            <IntegrationsSettingsLoader />
          )}
        </div>
      </section>
    </>
  );
}

export default observer(WorkspaceIntegrationsPage);
