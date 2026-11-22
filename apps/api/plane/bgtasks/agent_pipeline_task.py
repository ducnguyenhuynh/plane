# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from concurrent.futures import ThreadPoolExecutor, as_completed

# Django imports
from django.utils import timezone
from django.utils.html import escape

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import (
    AgentRun,
    AgentRunArtifact,
    AgentRunEvent,
    AgentRunStage,
    AgentRunStatus,
    AgentRunTask,
    AIAgent,
    Issue,
)
from plane.utils.agent_runtime import AgentRuntimeError, extract_json, invoke_agent
from plane.utils.exception_logger import log_exception

MAX_TASKS = 5


def log_event(run, kind, message="", stage=None, agent=None, data=None):
    AgentRunEvent.objects.create(
        run=run,
        stage=stage or run.stage,
        kind=kind,
        message=message,
        agent=agent,
        data=data or {},
    )


def issue_context(issue) -> str:
    description = (issue.description_stripped or "").strip()
    return (
        f"Work item: {issue.project.identifier}-{issue.sequence_id} {issue.name}\n"
        f"Priority: {issue.priority}\n"
        f"Description:\n{description[:6000] or '(no description)'}"
    )


def user_message(content: str) -> list:
    return [{"role": "user", "content": content}]


def create_child_issue(run, title, description):
    body = escape(description or "").replace("\n", "<br>")
    return Issue.objects.create(
        name=(title or "Subtask")[:255],
        description_html=f"<p>{body}</p>",
        project=run.project,
        workspace=run.workspace,
        parent=run.issue,
        state=run.issue.state,
        priority=run.issue.priority,
    )


def developer_agents(run):
    agents = list(AIAgent.objects.filter(id__in=run.developer_agents))
    if not agents:
        raise AgentRuntimeError("No developer agent is assigned to this project.")
    return agents


def plan_with_pm(run):
    """Ask up to pm_count managers in parallel and keep the plan with the most tasks."""
    agents = list(AIAgent.objects.filter(id__in=run.pm_agents))
    if not agents and run.pm_agent:
        agents = [run.pm_agent]
    if not agents:
        raise AgentRuntimeError("No project manager agent is assigned to this run.")

    prompt = (
        f"{issue_context(run.issue)}\n\n"
        "You are the project manager and tech lead. Break this work item into 1-3 concrete "
        "implementation subtasks for developers. Return ONLY JSON: "
        '{"summary": "...", "tasks": [{"title": "...", "description": "..."}]}'
    )

    def ask(agent):
        return agent, invoke_agent(agent, user_message(prompt))

    if len(agents) == 1:
        agent, text = ask(agents[0])
        return text, agent

    results = []
    with ThreadPoolExecutor(max_workers=len(agents)) as executor:
        futures = [executor.submit(ask, agent) for agent in agents]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                log_exception(e)
    if not results:
        raise AgentRuntimeError("All project manager agents failed to produce a plan.")

    def score(item):
        plan = extract_json(item[1]) or {}
        tasks = plan.get("tasks") if isinstance(plan, dict) else None
        return len(tasks) if isinstance(tasks, list) else 0

    agent, text = max(results, key=score)
    return text, agent


def plan_tasks(run, plan_text):
    """Create the child work items and run tasks from the PM plan, once per run."""
    if run.tasks.exists():
        return list(run.tasks.select_related("issue", "agent"))

    plan = extract_json(plan_text) or {}
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list) or not tasks:
        tasks = [{"title": run.issue.name, "description": run.issue.description_stripped or ""}]

    agents = developer_agents(run)
    created = []
    for index, item in enumerate(tasks[:MAX_TASKS]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"Subtask {index + 1}")[:255]
        description = str(item.get("description") or item.get("details") or "")[:4000]
        issue = create_child_issue(run, title, description)
        agent = agents[index % len(agents)]
        created.append(AgentRunTask.objects.create(run=run, issue=issue, agent=agent, status="pending"))
    if not created:
        issue = create_child_issue(run, run.issue.name, run.issue.description_stripped or "")
        created.append(AgentRunTask.objects.create(run=run, issue=issue, agent=agents[0], status="pending"))
    return created


def run_developer_stage(run):
    run.stage = AgentRunStage.DEVELOPER
    run.save(update_fields=["stage", "updated_at"])
    log_event(run, "stage", "Developers are implementing the subtasks.")
    tasks = list(run.tasks.select_related("issue", "agent").all())
    for task in tasks:
        if not task.agent:
            raise AgentRuntimeError(f"Subtask {task.issue.name} has no developer agent.")
        task.status = "running"
        task.save(update_fields=["status", "updated_at"])
        log_event(
            run,
            "stage",
            f"{task.agent.name} is working on {task.issue.name}.",
            stage=AgentRunStage.DEVELOPER,
            agent=task.agent,
            data={"task_id": str(task.id)},
        )

    def work(task):
        output = invoke_agent(
            task.agent,
            user_message(
                f"{issue_context(run.issue)}\n\n"
                f"Subtask: {task.issue.name}\n"
                f"Subtask details: {(task.issue.description_stripped or '')[:4000]}\n\n"
                "Implement this subtask. Return the complete, concrete implementation (code changes, a patch, or "
                "a technical design) in Markdown, with closed code fences. Keep it under 150 lines; do not ask "
                "questions."
            ),
            max_tokens=16384,
        )
        retried = False
        if not output.strip():
            # Reasoning models can spend the whole budget thinking and return no text
            retried = True
            output = invoke_agent(
                task.agent,
                user_message(
                    f"Subtask: {task.issue.name}\n"
                    f"Details: {(task.issue.description_stripped or '')[:2000]}\n\n"
                    "Return the concrete implementation in Markdown now."
                ),
                max_tokens=16384,
                effort_override="low",
            )
        return output, retried

    results = {}
    failures = []
    workers = max(1, len(run.developer_agents))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(work, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results[str(task.id)] = future.result()
            except Exception as e:
                failures.append((task, e))

    outputs = []
    for task in tasks:
        if str(task.id) not in results:
            task.status = "failed"
            task.save(update_fields=["status", "updated_at"])
            continue
        output, retried = results[str(task.id)]
        if retried:
            log_event(
                run,
                "info",
                f"{task.agent.name} returned no text on {task.issue.name}; retried with low effort.",
                stage=AgentRunStage.DEVELOPER,
                agent=task.agent,
            )
        if not output.strip():
            task.status = "failed"
            task.save(update_fields=["status", "updated_at"])
            failures.append(
                (
                    task,
                    AgentRuntimeError(
                        f"{task.agent.name} returned an empty answer for {task.issue.name}. "
                        "Lower its reasoning effort or use a model with a larger output budget."
                    ),
                )
            )
            continue
        task.output = output
        task.status = "done"
        task.save(update_fields=["output", "status", "updated_at"])
        AgentRunArtifact.objects.create(
            run=run,
            kind="subtask",
            title=f"{task.issue.project.identifier}-{task.issue.sequence_id} {task.issue.name}",
            content=output,
            data={"task_id": str(task.id), "issue_id": str(task.issue_id), "agent_id": str(task.agent_id or "")},
        )
        outputs.append(f"### {task.issue.name}\n{output}")

    if failures:
        task, error = failures[0]
        raise AgentRuntimeError(f"{task.issue.name}: {error}")
    return outputs


def run_qa_stage(run, developer_outputs):
    agents = list(AIAgent.objects.filter(id__in=run.qa_agents))
    if not agents and run.qa_agent:
        agents = [run.qa_agent]
    if not agents:
        raise AgentRuntimeError("No QA agent is assigned to this run.")
    run.stage = AgentRunStage.QA
    run.save(update_fields=["stage", "updated_at"])
    log_event(
        run,
        "stage",
        f"QA is reviewing the implementation ({len(agents)} reviewer(s)).",
        stage=AgentRunStage.QA,
        agent=run.qa_agent,
    )

    prompt = (
        f"{issue_context(run.issue)}\n\n"
        f"Developer outputs:\n{'\n\n'.join(developer_outputs)[:14000]}\n\n"
        "Review the outputs against the work item. The outputs are implementation proposals, not code already "
        "merged in a repository. Approve when they contain a concrete, plausible implementation of the core "
        "requirement; minor gaps are acceptable. Reject only when the core implementation is missing, empty, "
        'or contradictory. Return ONLY JSON: {"passed": true|false, "notes": "..."}'
    )

    def review(agent):
        return agent, invoke_agent(agent, user_message(prompt), max_tokens=2048)

    reviews = []
    if len(agents) == 1:
        reviews.append(review(agents[0]))
    else:
        with ThreadPoolExecutor(max_workers=len(agents)) as executor:
            futures = [executor.submit(review, agent) for agent in agents]
            for future in as_completed(futures):
                reviews.append(future.result())

    rejections = []
    for agent, review_text in reviews:
        review = extract_json(review_text) or {"passed": False, "notes": review_text}
        passed = bool(review.get("passed"))
        AgentRunArtifact.objects.create(
            run=run,
            kind="review",
            title=f"QA review — {agent.name}",
            content=review_text,
            data={"passed": passed, "agent_id": str(agent.id)},
        )
        if not passed:
            rejections.append(f"{agent.name}: {str(review.get('notes') or '')[:800]}")

    if rejections:
        raise AgentRuntimeError(f"QA rejected the result: {' | '.join(rejections)[:2000]}")
    log_event(run, "stage", "QA approved the result.", stage=AgentRunStage.QA, agent=run.qa_agent)


def run_final_stage(run, developer_outputs):
    if not run.pm_agent:
        raise AgentRuntimeError("No project manager agent is assigned to this run.")
    run.stage = AgentRunStage.FINAL
    run.save(update_fields=["stage", "updated_at"])
    log_event(run, "stage", "Final summary is being written.", stage=AgentRunStage.FINAL, agent=run.pm_agent)

    plan = run.artifacts.filter(kind="plan").order_by("-created_at").first()
    review = run.artifacts.filter(kind="review").order_by("-created_at").first()
    final_text = invoke_agent(
        run.pm_agent,
        user_message(
            f"{issue_context(run.issue)}\n\n"
            f"Project plan:\n{(plan.content if plan else '')[:4000]}\n\n"
            f"Implementation outputs:\n{'\n\n'.join(developer_outputs)[:12000]}\n\n"
            f"QA review:\n{(review.content if review else 'QA approved the outputs.')[:2000]}\n\n"
            "Write the final result for this work item: what was done, key decisions, and remaining risks. "
            "Use Markdown and do not use placeholders."
        ),
        max_tokens=4096,
    )
    run.result = final_text
    AgentRunArtifact.objects.create(run=run, kind="final", title="Final result", content=final_text)
    run.status = AgentRunStatus.COMPLETED
    run.stage = AgentRunStage.DONE
    run.completed_at = timezone.now()
    run.save()


@shared_task
def run_agent_pipeline(run_id):
    """PM plan -> child work items -> developer output per task -> QA -> final summary."""
    try:
        run = AgentRun.objects.select_related("issue", "issue__project", "pm_agent", "qa_agent").get(pk=run_id)
    except AgentRun.DoesNotExist:
        return

    run.status = AgentRunStatus.RUNNING
    run.started_at = run.started_at or timezone.now()
    run.error = ""
    run.save(update_fields=["status", "started_at", "error", "updated_at"])
    log_event(run, "status", "Agent run started.")

    try:
        developer_outputs = []
        if not run.tasks.exists():
            run.stage = AgentRunStage.PM
            run.save(update_fields=["stage", "updated_at"])
            log_event(run, "stage", "Project manager is reviewing the work item.", agent=run.pm_agent)
            plan_text, chosen_pm = plan_with_pm(run)
            if chosen_pm.id != run.pm_agent_id:
                log_event(run, "info", f"Plan from {chosen_pm.name} selected.", agent=chosen_pm)
            AgentRunArtifact.objects.create(
                run=run,
                kind="plan",
                title="Project plan",
                content=plan_text,
                data={"agent_id": str(chosen_pm.id)},
            )
            tasks = plan_tasks(run, plan_text)
            log_event(
                run,
                "stage",
                f"PM created {len(tasks)} subtask(s).",
                data={"task_ids": [str(task.id) for task in tasks]},
            )

        developer_outputs = run_developer_stage(run)
        run_qa_stage(run, developer_outputs)
        run_final_stage(run, developer_outputs)
        log_event(run, "status", "Agent run completed.")
    except Exception as e:
        log_exception(e)
        run.status = AgentRunStatus.FAILED
        run.error = str(e)[:2000]
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error", "completed_at", "updated_at"])
        log_event(run, "error", str(e)[:2000])
