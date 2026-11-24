from urllib.parse import urlsplit

from django.db import migrations

OPENCODE_BASE_URL = "https://opencode.ai/inference/go/v1"


def move_agents_to_opencode(apps, schema_editor):
    """Make OpenCode the only usable provider, keeping every row.

    Rows already pointing at an OpenCode inference endpoint are switched to the canonical URL; every
    other row, an OpenCode one with another endpoint included, is benched instead of deleted, so its key
    and history stay intact. Soft-deleted rows are included.
    """
    AIAgent = apps.get_model("db", "AIAgent")
    # The historical model drops the soft-delete manager, so _base_manager sees every row
    for agent in AIAgent._base_manager.iterator():
        if agent.provider == "opencode" and not agent.base_url:
            # An empty base URL already runs against the default endpoint
            continue
        url = urlsplit(agent.base_url or "")
        if url.hostname == "opencode.ai" and url.path.startswith("/inference/go/"):
            AIAgent.objects.filter(pk=agent.pk).update(provider="opencode", base_url=OPENCODE_BASE_URL)
        else:
            AIAgent.objects.filter(pk=agent.pk).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0132_agent_run_sessions"),
    ]

    operations = [
        migrations.RunPython(move_agents_to_opencode, migrations.RunPython.noop),
    ]
