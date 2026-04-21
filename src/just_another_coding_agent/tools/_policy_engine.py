from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionKind = Literal["filesystem_read", "network_access", "filesystem_write"]
ActionSource = Literal["shell"]
PathScope = Literal["workspace", "non_workspace"]
DecisionKind = Literal["allow", "prompt", "deny"]


@dataclass(frozen=True)
class PermissionAction:
    action_kind: ActionKind
    source: ActionSource
    path_scope: PathScope | None = None
    root: str | None = None
    covered_by_current_permissions: bool = False
    extracted_by: str | None = None


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    description: str
    action_kind: ActionKind
    source: ActionSource | None = None
    path_scope: PathScope | None = None
    covered_by_current_permissions: bool | None = None
    decision: DecisionKind = "prompt"


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    reason: str
    decision: DecisionKind


@dataclass(frozen=True)
class PolicyEvaluationResult:
    action: PermissionAction
    match: RuleMatch


SHELL_POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        rule_id="allow-shell-workspace-read",
        description="Allow shell reads inside the workspace",
        action_kind="filesystem_read",
        source="shell",
        path_scope="workspace",
        decision="allow",
    ),
    PolicyRule(
        rule_id="allow-shell-non-workspace-read-when-covered",
        description=(
            "Allow shell reads outside the workspace when current permissions "
            "already cover them"
        ),
        action_kind="filesystem_read",
        source="shell",
        path_scope="non_workspace",
        covered_by_current_permissions=True,
        decision="allow",
    ),
    PolicyRule(
        rule_id="prompt-shell-non-workspace-read-when-uncovered",
        description=(
            "Prompt for shell reads outside the workspace when current "
            "permissions do not cover them"
        ),
        action_kind="filesystem_read",
        source="shell",
        path_scope="non_workspace",
        covered_by_current_permissions=False,
        decision="prompt",
    ),
    PolicyRule(
        rule_id="allow-shell-network-when-covered",
        description=(
            "Allow shell network access when current permissions already cover "
            "it"
        ),
        action_kind="network_access",
        source="shell",
        covered_by_current_permissions=True,
        decision="allow",
    ),
    PolicyRule(
        rule_id="prompt-shell-network-when-uncovered",
        description=(
            "Prompt for shell network access when current permissions do not "
            "cover it"
        ),
        action_kind="network_access",
        source="shell",
        covered_by_current_permissions=False,
        decision="prompt",
    ),
    PolicyRule(
        rule_id="allow-shell-workspace-write",
        description="Allow shell writes inside the workspace",
        action_kind="filesystem_write",
        source="shell",
        path_scope="workspace",
        decision="allow",
    ),
    PolicyRule(
        rule_id="allow-shell-non-workspace-write-when-covered",
        description=(
            "Allow shell writes outside the workspace when current "
            "permissions already cover them"
        ),
        action_kind="filesystem_write",
        source="shell",
        path_scope="non_workspace",
        covered_by_current_permissions=True,
        decision="allow",
    ),
    PolicyRule(
        rule_id="prompt-shell-non-workspace-write-when-uncovered",
        description=(
            "Prompt for shell writes outside the workspace when current "
            "permissions do not cover them"
        ),
        action_kind="filesystem_write",
        source="shell",
        path_scope="non_workspace",
        covered_by_current_permissions=False,
        decision="prompt",
    ),
)


def _rule_matches(*, action: PermissionAction, rule: PolicyRule) -> bool:
    if action.action_kind != rule.action_kind:
        return False
    if rule.source is not None and action.source != rule.source:
        return False
    if rule.path_scope is not None and action.path_scope != rule.path_scope:
        return False
    if (
        rule.covered_by_current_permissions is not None
        and action.covered_by_current_permissions
        != rule.covered_by_current_permissions
    ):
        return False
    return True


def evaluate_permission_actions(
    *,
    actions: tuple[PermissionAction, ...],
    rules: tuple[PolicyRule, ...] = SHELL_POLICY_RULES,
) -> tuple[PolicyEvaluationResult, ...]:
    evaluations: list[PolicyEvaluationResult] = []
    for action in actions:
        for rule in rules:
            if _rule_matches(action=action, rule=rule):
                evaluations.append(
                    PolicyEvaluationResult(
                        action=action,
                        match=RuleMatch(
                            rule_id=rule.rule_id,
                            reason=rule.description,
                            decision=rule.decision,
                        ),
                    )
                )
                break
        else:
            evaluations.append(
                PolicyEvaluationResult(
                    action=action,
                    match=RuleMatch(
                        rule_id="no-matching-policy-rule",
                        reason="No explicit policy rule matched; deny by default",
                        decision="deny",
                    ),
                )
            )
    return tuple(evaluations)


__all__ = [
    "PermissionAction",
    "PolicyRule",
    "PolicyEvaluationResult",
    "RuleMatch",
    "SHELL_POLICY_RULES",
    "evaluate_permission_actions",
]
