# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.utils import timezone
from django.utils.html import escape

# Third party imports
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

# Module imports
from plane.app.serializers import IssueDetailSerializer
from plane.bgtasks.agent_slack_task import send_agent_run_slack_message
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.webhook_task import model_activity
from plane.db.models import (
    AgentRun,
    AgentRunArtifact,
    AgentRunEvent,
    AgentRunStage,
    AgentRunStatus,
    AgentRunTask,
    AIAgent,
    Issue,
    IssueAssignee,
    IssueComment,
    State,
    StateGroup,
)
from plane.utils.agent_bot import ensure_agent_bot, ensure_project_member
from plane.utils.agent_runtime import AgentRuntimeError, extract_json, invoke_agent, markdown_to_html
from plane.utils.exception_logger import log_exception

MAX_TASKS = 10
MAX_REWORK_ROUNDS = 1
STALE_RUN_MINUTES = 35
STALE_RUN_ERROR = "Run timed out or the worker stopped."
PIPELINE_SOFT_TIME_LIMIT = 1800
PIPELINE_TIME_LIMIT = 1900


class QaRejectedError(AgentRuntimeError):
    """QA did not approve; carries the notes the developers must address."""

    def __init__(self, message, notes=""):
        super().__init__(message)
        self.notes = notes


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


def in_thread(func):
    """Run a pipeline worker in a thread, releasing its DB connection when done."""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            connection.close()

    return wrapper


def load_agents(agent_ids):
    """Load agents in snapshot order instead of the model's default ordering."""
    agents = {str(agent.id): agent for agent in AIAgent.objects.filter(id__in=list(agent_ids))}
    return [agents[str(agent_id)] for agent_id in agent_ids if str(agent_id) in agents]


def pm_agents(run):
    agents = load_agents(run.pm_agents or [])
    if not agents and run.pm_agent:
        agents = [run.pm_agent]
    if not agents:
        raise AgentRuntimeError("No project manager agent is assigned to this run.")
    return agents


def developer_agents(run):
    agents = load_agents(run.developer_agents or [])
    if not agents:
        raise AgentRuntimeError("No developer agent is assigned to this project.")
    return agents


def reviewer_agents(run):
    agents = load_agents(run.qa_agents or [])
    if not agents and run.qa_agent:
        agents = [run.qa_agent]
    if not agents:
        raise AgentRuntimeError("No QA agent is assigned to this run.")
    return agents


def agent_model(run, agent) -> str | None:
    """The model snapshot taken at run start (a position's model_override wins)."""
    return (run.agent_models or {}).get(str(agent.id)) or None


def invoke(run, agent, messages, max_tokens=4096, effort_override=None):
    return invoke_agent(
        agent, messages, max_tokens=max_tokens, effort_override=effort_override, model=agent_model(run, agent)
    )


def max_plan_tasks(run) -> int:
    """N = min(MAX_TASKS, max(3, developer count))."""
    return min(MAX_TASKS, max(3, len(run.developer_agents or [])))


def plan_tasks_from(text, max_tasks):
    """The valid subtasks in a PM answer, capped at max_tasks. Pure."""
    plan = extract_json(text) or {}
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        return []
    return [task for task in tasks if isinstance(task, dict)][:max_tasks]


def select_plan(candidates, max_tasks):
    """Pick the plan with the most valid subtasks; ties keep the first (lead) PM. Pure."""
    best = None
    for agent, text in candidates:
        tasks = plan_tasks_from(text, max_tasks)
        if best is None or len(tasks) > len(best[2]):
            best = (agent, text, tasks)
    return best


def group_tasks_by_agent(tasks):
    """Group tasks per agent, keeping first-seen order. Pure."""
    groups = {}
    order = []
    for task in tasks:
        key = task.agent_id
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(task)
    return [(groups[key][0].agent, groups[key]) for key in order]


def is_cancelled(run) -> bool:
    """Re-read the status so a cancelled run stops between stages and between tasks."""
    return AgentRun.objects.filter(pk=run.pk, status=AgentRunStatus.FAILED).exists()


def change_issue_state(issue, group, actor, slug):
    """Move a work item to the project's first state in a group, reporting the change."""
    state = State.objects.filter(project_id=issue.project_id, group=group).order_by("sequence").first()
    if not state or issue.state_id == state.id:
        return

    current_instance = json.dumps(IssueDetailSerializer(issue).data, cls=DjangoJSONEncoder)
    issue.state = state
    # update_fields skips auto_now, so updated_at has to be listed to move
    issue.save(update_fields=["state", "updated_at"])
    issue_activity.delay(
        type="issue.activity.updated",
        requested_data=json.dumps({"state_id": str(state.id)}, cls=DjangoJSONEncoder),
        actor_id=str(actor.id),
        issue_id=str(issue.id),
        project_id=str(issue.project_id),
        current_instance=current_instance,
        epoch=int(timezone.now().timestamp()),
        notification=True,
        origin=None,
    )
    model_activity.delay(
        model_name="issue",
        model_id=str(issue.id),
        requested_data={"state_id": str(state.id)},
        current_instance=current_instance,
        actor_id=actor.id,
        slug=slug,
        origin=None,
    )


def comment_key(run_id, kind, task_id, rework_round) -> str:
    """Identifies one pipeline comment, so a manual retry does not post it twice. Pure."""
    return f"{run_id}:{kind}:{task_id or 'none'}:{rework_round}"


def post_comment(run, issue, actor, text, kind="note", task=None):
    """Post a Markdown comment as the given bot, once per run + kind + task + round."""
    key = comment_key(run.id, kind, getattr(task, "id", None), run.rework_round)
    if AgentRunEvent.objects.filter(run=run, kind="comment", data__key=key).exists():
        return None

    comment = IssueComment(
        issue=issue,
        project_id=issue.project_id,
        workspace_id=issue.workspace_id,
        actor=actor,
        comment_html=markdown_to_html(text),
    )
    # BaseModel.save ignores created_by_id when disable_auto_set_user is set, so set both
    comment.created_by_id = actor.id
    comment.save(created_by_id=actor.id)
    log_event(run, "comment", "Comment posted.", data={"key": key, "comment_id": str(comment.id)})
    issue_activity.delay(
        type="comment.activity.created",
        requested_data=json.dumps(
            {"id": str(comment.id), "comment_html": comment.comment_html, "actor_id": str(actor.id)},
            cls=DjangoJSONEncoder,
        ),
        actor_id=str(actor.id),
        issue_id=str(issue.id),
        project_id=str(issue.project_id),
        current_instance=None,
        epoch=int(timezone.now().timestamp()),
        notification=True,
        origin=None,
    )
    return comment


def create_child_issue(run, title, description, creator_bot, assignee_bot):
    body = escape(description or "").replace("\n", "<br>")
    issue = Issue(
        name=(title or "Subtask")[:255],
        description_html=f"<p>{body}</p>",
        project=run.project,
        workspace=run.workspace,
        parent=run.issue,
        state=run.issue.state,
        priority=run.issue.priority,
        created_by_id=creator_bot.id,
    )
    issue.save(created_by_id=creator_bot.id)

    assignee = IssueAssignee(
        issue=issue,
        assignee=assignee_bot,
        project=run.project,
        workspace=run.workspace,
    )
    assignee.save(created_by_id=creator_bot.id)
    issue_activity.delay(
        type="issue.activity.created",
        requested_data=json.dumps(
            {
                "name": issue.name,
                "state_id": str(issue.state_id),
                "priority": issue.priority,
                "parent_id": str(run.issue_id),
                "assignee_ids": [str(assignee_bot.id)],
            },
            cls=DjangoJSONEncoder,
        ),
        actor_id=str(creator_bot.id),
        issue_id=str(issue.id),
        project_id=str(run.project_id),
        current_instance=None,
        epoch=int(timezone.now().timestamp()),
        notification=True,
        origin=None,
    )
    model_activity.delay(
        model_name="issue",
        model_id=str(issue.id),
        requested_data={"name": issue.name, "state_id": str(issue.state_id)},
        current_instance=None,
        actor_id=creator_bot.id,
        slug=run.workspace.slug,
        origin=None,
    )
    return issue


def plan_with_pm(run):
    """Ask every PM in parallel and keep the plan with the most valid subtasks."""
    agents = pm_agents(run)
    limit = max_plan_tasks(run)
    prompt = (
        f"{issue_context(run.issue)}\n\n"
        "You are the project manager and tech lead. Break this work item into between 1 and "
        f"{limit} concrete implementation subtasks for developers. "
        'Return ONLY JSON: {"summary": "...", "tasks": [{"title": "...", "description": "..."}]}'
    )

    @in_thread
    def ask(agent):
        return invoke(run, agent, user_message(prompt))

    texts = {}
    if len(agents) == 1:
        texts[str(agents[0].id)] = invoke(run, agents[0], user_message(prompt))
    else:
        with ThreadPoolExecutor(max_workers=len(agents)) as executor:
            futures = {executor.submit(ask, agent): agent for agent in agents}
            for future in as_completed(futures):
                agent = futures[future]
                try:
                    texts[str(agent.id)] = future.result()
                except Exception as e:
                    log_exception(e)
                    log_event(run, "error", f"{agent.name} could not produce a plan: {e}", agent=agent)

    # Snapshot order, so ties always resolve to the lead PM
    candidates = [(agent, texts[str(agent.id)]) for agent in agents if str(agent.id) in texts]
    if not candidates:
        raise AgentRuntimeError("All project manager agents failed to produce a plan.")

    chosen = select_plan(candidates, limit)
    for agent, text in candidates:
        AgentRunArtifact.objects.create(
            run=run,
            kind="plan_candidate",
            title=f"Plan candidate — {agent.name}",
            content=text,
            data={"agent_id": str(agent.id), "task_count": len(plan_tasks_from(text, limit))},
        )
    return chosen


def plan_tasks(run, plan, agents, pm_bot):
    """Create the child work items and run tasks from the chosen plan, once per run."""
    if run.tasks.exists():
        return list(run.tasks.select_related("issue", "agent"))

    created = []
    for index, item in enumerate(plan or []):
        title = str(item.get("title") or f"Subtask {index + 1}")[:255]
        description = str(item.get("description") or item.get("details") or "")[:4000]
        agent = agents[index % len(agents)]
        assignee = ensure_agent_bot(agent)
        ensure_project_member(assignee, run.project)
        issue = create_child_issue(run, title, description, pm_bot, assignee)
        created.append(AgentRunTask.objects.create(run=run, issue=issue, agent=agent, status="pending"))

    if not created:
        agent = agents[0]
        assignee = ensure_agent_bot(agent)
        ensure_project_member(assignee, run.project)
        issue = create_child_issue(run, run.issue.name, run.issue.description_stripped or "", pm_bot, assignee)
        created.append(AgentRunTask.objects.create(run=run, issue=issue, agent=agent, status="pending"))
    return created


def developer_prompt(run, task, qa_notes=""):
    notes = f"\n\nQA notes from the previous review (address these):\n{qa_notes[:4000]}" if qa_notes else ""
    return (
        f"{issue_context(run.issue)}\n\n"
        f"Subtask: {task.issue.name}\n"
        f"Subtask details: {(task.issue.description_stripped or '')[:4000]}{notes}\n\n"
        "Implement this subtask. Return the complete, concrete implementation (code changes, a patch, or "
        "a technical design) in Markdown, with closed code fences. Keep it under 150 lines; do not ask "
        "questions."
    )


def move_task_state(run, task, group, actor):
    try:
        change_issue_state(task.issue, group, actor, run.workspace.slug)
    except Exception as e:
        log_exception(e)


def work_task(run, task, qa_notes=""):
    """Run one subtask on its agent; it only starts now, one task at a time per agent."""
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
    bot = ensure_agent_bot(task.agent)
    move_task_state(run, task, StateGroup.STARTED, bot)

    output = invoke(run, task.agent, user_message(developer_prompt(run, task, qa_notes)), max_tokens=16384)
    retried = False
    if not output.strip():
        # Reasoning models can spend the whole budget thinking and return no text
        retried = True
        output = invoke(
            run,
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


@in_thread
def work_agent_tasks(run, agent, tasks, qa_notes=""):
    """One worker per agent: its tasks run sequentially, agents run in parallel."""
    results = {}
    for task in tasks:
        if is_cancelled(run):
            return results
        try:
            results[str(task.id)] = work_task(run, task, qa_notes)
        except Exception as e:
            results[str(task.id)] = e
    return results


def run_developer_stage(run, qa_notes="", rework=False):
    run.stage = AgentRunStage.DEVELOPER
    run.save(update_fields=["stage", "updated_at"])
    log_event(run, "stage", "Developers are implementing the subtasks.")
    tasks = list(run.tasks.select_related("issue", "agent").all())

    pending = []
    for task in tasks:
        if not task.agent:
            raise AgentRuntimeError(f"Subtask {task.issue.name} has no developer agent.")
        # A retry must not redo finished work; a QA rework round must
        if task.status == "done" and task.output.strip() and not rework:
            continue
        pending.append(task)

    workers = [group for group in group_tasks_by_agent(pending) if group[0] is not None]
    collected = {}
    with ThreadPoolExecutor(max_workers=max(1, len(workers))) as executor:
        futures = {
            executor.submit(work_agent_tasks, run, agent, agent_tasks, qa_notes): agent
            for agent, agent_tasks in workers
        }
        for future in as_completed(futures):
            try:
                collected.update(future.result())
            except Exception as e:
                log_exception(e)

    failures = []
    for task in tasks:
        if is_cancelled(run):
            return []
        if task.status == "done" and task.output.strip() and not rework:
            continue
        result = collected.get(str(task.id))
        if not isinstance(result, tuple):
            task.status = "failed"
            task.save(update_fields=["status", "updated_at"])
            failures.append((task, result if isinstance(result, Exception) else AgentRuntimeError("Not run.")))
            continue

        output, retried = result
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
        bot = ensure_agent_bot(task.agent)
        post_comment(run, task.issue, bot, output, kind="subtask", task=task)
        if not run.artifacts.filter(kind="subtask", data__task_id=str(task.id), data__round=run.rework_round).exists():
            AgentRunArtifact.objects.create(
                run=run,
                kind="subtask",
                title=f"{task.issue.project.identifier}-{task.issue.sequence_id} {task.issue.name}",
                content=output,
                data={
                    "task_id": str(task.id),
                    "issue_id": str(task.issue_id),
                    "agent_id": str(task.agent_id or ""),
                    "round": run.rework_round,
                },
            )

    if failures:
        task, error = failures[0]
        raise AgentRuntimeError(f"{task.issue.name}: {error}")
    return [f"### {task.issue.name}\n{task.output}" for task in tasks if task.status == "done" and task.output.strip()]


def review_prompt(run, developer_outputs, qa_notes=""):
    previous = f"\n\nNotes from the previous review round:\n{qa_notes[:4000]}" if qa_notes else ""
    return (
        f"{issue_context(run.issue)}\n\n"
        f"Developer outputs:\n{'\n\n'.join(developer_outputs)[:14000]}{previous}\n\n"
        "Review the outputs against the work item. The outputs are implementation proposals, not code already "
        "merged in a repository. Approve when they contain a concrete, plausible implementation of the core "
        "requirement; minor gaps are acceptable. Reject only when the core implementation is missing, empty, "
        'or contradictory. Return ONLY JSON: {"passed": true|false, "notes": "..."}'
    )


def run_qa_stage(run, developer_outputs, qa_notes=""):
    """Every reviewer reviews in parallel; a reviewer that raises is logged and ignored."""
    agents = reviewer_agents(run)
    run.stage = AgentRunStage.QA
    run.save(update_fields=["stage", "updated_at"])
    log_event(
        run,
        "stage",
        f"QA is reviewing the implementation ({len(agents)} reviewer(s)).",
        stage=AgentRunStage.QA,
        agent=run.qa_agent,
    )
    prompt = review_prompt(run, developer_outputs, qa_notes)

    @in_thread
    def review(agent):
        return agent, invoke(run, agent, user_message(prompt), max_tokens=2048)

    reviews = []
    if len(agents) == 1:
        try:
            reviews.append((agents[0], invoke(run, agents[0], user_message(prompt), max_tokens=2048)))
        except Exception as e:
            log_exception(e)
            log_event(run, "error", f"{agents[0].name} failed to review: {e}", stage=AgentRunStage.QA, agent=agents[0])
    else:
        with ThreadPoolExecutor(max_workers=len(agents)) as executor:
            futures = {executor.submit(review, agent): agent for agent in agents}
            for future in as_completed(futures):
                agent = futures[future]
                try:
                    reviews.append(future.result())
                except Exception as e:
                    log_exception(e)
                    log_event(run, "error", f"{agent.name} failed to review: {e}", stage=AgentRunStage.QA, agent=agent)

    if not reviews:
        raise AgentRuntimeError("No QA reviewer produced a review.")

    rejections = []
    notes = []
    for agent, review_text in reviews:
        verdict = extract_json(review_text) or {"passed": False, "notes": review_text}
        passed = bool(verdict.get("passed"))
        AgentRunArtifact.objects.create(
            run=run,
            kind="review",
            title=f"QA review — {agent.name}",
            content=review_text,
            data={"passed": passed, "agent_id": str(agent.id), "round": run.rework_round},
        )
        post_comment(
            run,
            run.issue,
            ensure_agent_bot(agent),
            f"**QA verdict from {agent.name}: {'approved' if passed else 'changes requested'}**\n\n"
            f"{str(verdict.get('notes') or '')[:4000]}",
            kind=f"review:{agent.id}",
        )
        if not passed:
            rejections.append(f"{agent.name}: {str(verdict.get('notes') or '')[:800]}")
            notes.append(f"{agent.name}: {str(verdict.get('notes') or '')[:1500]}")

    if rejections:
        if not notes:
            raise QaRejectedError("QA rejected the result.")
        raise QaRejectedError(f"QA rejected the result: {' | '.join(rejections)[:2000]}", "\n\n".join(notes))

    log_event(run, "stage", "QA approved the result.", stage=AgentRunStage.QA, agent=run.qa_agent)
    qa_bot = ensure_agent_bot(agents[0])
    for task in run.tasks.select_related("issue").filter(status="done"):
        move_task_state(run, task, StateGroup.COMPLETED, qa_bot)


def latest_review_notes(run) -> str:
    """QA notes of the current round, newest first."""
    reviews = run.artifacts.filter(kind="review").order_by("-created_at")[:10]
    return "\n\n".join(review.content[:1500] for review in reviews if review.data.get("round") == run.rework_round)[
        :4000
    ]


def planned_by(run):
    """The PM whose plan was chosen; a resumed run must not fall back to the lead PM."""
    plan = run.artifacts.filter(kind="plan").order_by("-created_at").first()
    agent_id = (plan.data or {}).get("agent_id") if plan else None
    if not agent_id:
        return run.pm_agent
    return AIAgent.objects.filter(pk=agent_id).first() or run.pm_agent


def run_final_stage(run, developer_outputs, pm_agent):
    if not pm_agent:
        raise AgentRuntimeError("No project manager agent is assigned to this run.")
    run.stage = AgentRunStage.FINAL
    run.save(update_fields=["stage", "updated_at"])
    log_event(run, "stage", "Final summary is being written.", stage=AgentRunStage.FINAL, agent=pm_agent)

    plan = run.artifacts.filter(kind="plan").order_by("-created_at").first()
    reviews = list(run.artifacts.filter(kind="review").order_by("-created_at")[:10])
    latest = [review for review in reviews if review.data.get("round") == run.rework_round] or reviews[:1]
    review_text = "\n\n".join(review.content[:2000] for review in latest) or "QA approved the outputs."
    final_text = invoke(
        run,
        pm_agent,
        user_message(
            f"{issue_context(run.issue)}\n\n"
            f"Project plan:\n{(plan.content if plan else '')[:4000]}\n\n"
            f"Implementation outputs:\n{'\n\n'.join(developer_outputs)[:12000]}\n\n"
            f"QA reviews:\n{review_text}\n\n"
            "Write the final result for this work item: what was done, key decisions, and remaining risks. "
            "Use Markdown and do not use placeholders."
        ),
        max_tokens=4096,
    )
    run.result = final_text
    AgentRunArtifact.objects.create(run=run, kind="final", title="Final result", content=final_text)
    post_comment(run, run.issue, ensure_agent_bot(pm_agent), final_text, kind="final")
    run.status = AgentRunStatus.COMPLETED
    run.stage = AgentRunStage.DONE
    run.completed_at = timezone.now()
    run.save()


def fail_run(run, message):
    run.status = AgentRunStatus.FAILED
    run.error = str(message)[:2000]
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "error", "completed_at", "updated_at"])
    log_event(run, "error", str(message)[:2000])


def notify_slack(run, kind, message):
    """Slack through a Celery task, so the pipeline never blocks on it."""
    try:
        send_agent_run_slack_message.delay(str(run.id), kind, message)
    except Exception as e:
        log_exception(e)


def pipeline(run):
    if run.pm_agent and run.issue.state and run.issue.state.group in (StateGroup.BACKLOG, StateGroup.UNSTARTED):
        change_issue_state(run.issue, StateGroup.STARTED, ensure_agent_bot(run.pm_agent), run.workspace.slug)

    has_tasks = run.tasks.exists()
    pm_agent = planned_by(run) if has_tasks else run.pm_agent
    if not has_tasks:
        run.stage = AgentRunStage.PM
        run.save(update_fields=["stage", "updated_at"])
        log_event(run, "stage", "Project manager is reviewing the work item.", agent=run.pm_agent)
        pm_agent, plan_text, plan = plan_with_pm(run)
        if pm_agent.id != run.pm_agent_id:
            log_event(run, "info", f"Plan from {pm_agent.name} selected.", agent=pm_agent)
        AgentRunArtifact.objects.create(
            run=run,
            kind="plan",
            title="Project plan",
            content=plan_text,
            data={"agent_id": str(pm_agent.id)},
        )
        created = plan_tasks(run, plan, developer_agents(run), ensure_agent_bot(pm_agent))
        log_event(
            run,
            "stage",
            f"PM created {len(created)} subtask(s).",
            data={"task_ids": [str(task.id) for task in created]},
        )
        summary = (extract_json(plan_text) or {}).get("summary") or "Work is split into subtasks."
        post_comment(
            run,
            run.issue,
            ensure_agent_bot(pm_agent),
            f"**Plan**\n\n{summary}\n\n" + "\n".join(f"- {task.issue.name}" for task in created),
            kind="plan",
        )

    # A retried run resumes with the QA notes that failed it
    notes = latest_review_notes(run)
    developer_outputs = run_developer_stage(run, notes)
    if is_cancelled(run):
        return

    while True:
        try:
            run_qa_stage(run, developer_outputs, notes)
            break
        except QaRejectedError as e:
            if run.rework_round >= MAX_REWORK_ROUNDS:
                raise
            run.rework_round += 1
            run.save(update_fields=["rework_round", "updated_at"])
            notes = e.notes
            log_event(
                run,
                "stage",
                f"QA requested changes; sending the notes back to the developers (round {run.rework_round}).",
                stage=AgentRunStage.QA,
            )
            run.stage = AgentRunStage.DEVELOPER
            run.save(update_fields=["stage", "updated_at"])
            developer_outputs = run_developer_stage(run, notes, rework=True)
            if is_cancelled(run):
                return
    if is_cancelled(run):
        return
    run_final_stage(run, developer_outputs, pm_agent)


@shared_task(soft_time_limit=PIPELINE_SOFT_TIME_LIMIT, time_limit=PIPELINE_TIME_LIMIT)
def run_agent_pipeline(run_id):
    """PM plan -> child work items -> developer output per task -> QA -> final summary."""
    try:
        run = AgentRun.objects.select_related("issue", "issue__project", "pm_agent", "qa_agent").get(pk=run_id)
    except AgentRun.DoesNotExist:
        return

    # Only a queued run may start; anything else (cancelled, running elsewhere) is left alone
    started = AgentRun.objects.filter(pk=run.pk, status=AgentRunStatus.QUEUED).update(
        status=AgentRunStatus.RUNNING,
        started_at=timezone.now(),
        error="",
        updated_at=timezone.now(),
    )
    if not started:
        return
    run.refresh_from_db()

    try:
        for agent in load_agents((run.pm_agents or []) + (run.developer_agents or []) + (run.qa_agents or [])):
            ensure_agent_bot(agent)
        log_event(run, "status", "Agent run started.")
        notify_slack(run, "started", f"Agent run started on {run.issue.name}.")
        pipeline(run)
        if not is_cancelled(run):
            log_event(run, "status", "Agent run completed.")
            notify_slack(run, "completed", f"Agent run completed on {run.issue.name}.")
    except SoftTimeLimitExceeded:
        fail_run(run, "The agent run was stopped: it exceeded the worker time limit.")
        notify_slack(run, "failed", "The agent run was stopped: it exceeded the worker time limit.")
    except Exception as e:
        if is_cancelled(run):
            return
        log_exception(e)
        fail_run(run, str(e))
        notify_slack(run, "failed", str(e))
        if run.pm_agent and run.pm_agent.bot_user_id:
            try:
                post_comment(
                    run, run.issue, run.pm_agent.bot_user, f"**The agent run failed**\n\n{str(e)[:2000]}", kind="error"
                )
            except Exception as comment_error:
                log_exception(comment_error)


def mark_stale_runs_failed(project):
    """Close runs whose worker died, so the work item is not blocked forever."""
    cutoff = timezone.now() - timedelta(minutes=STALE_RUN_MINUTES)
    return AgentRun.objects.filter(
        project=project,
        status__in=[AgentRunStatus.QUEUED, AgentRunStatus.RUNNING],
        updated_at__lt=cutoff,
    ).update(status=AgentRunStatus.FAILED, error=STALE_RUN_ERROR, completed_at=timezone.now())
