# ChatGPT Subscription OAuth Spike

read_when: you want to prove ChatGPT subscription OAuth is feasible in JACA without committing to full multi-provider OAuth product work

## Goal

Prove one narrow end-to-end path:

1. JACA can start ChatGPT OAuth login from the TUI.
2. The backend can persist and later refresh the returned credentials.
3. One OAuth-backed OpenAI Codex model can run successfully with those credentials.

This is a feasibility spike, not a product rollout.

## Non-Goals

- No generic multi-provider OAuth framework in the first slice.
- No Anthropic support in the first slice.
- No migration of existing local secret storage.
- No broad auth/settings UI redesign.
- No promise that the final product surface will keep the same command names.

## Why This Provider First

- Highest-value subscription case.
- Browser callback flow is cleaner than a device-code flow.
- pi-mono already shows a workable pattern for OpenAI Codex OAuth:
  - provider-specific OAuth flow in `packages/ai`
  - coding-agent-owned storage and UI

We should borrow the boundary, not the architecture:

- backend owns auth semantics and credential persistence
- TUI only drives the focused interaction flow

## What Must Be Proven

The spike is successful only if all of these are true:

- login can be initiated from the JACA TUI
- browser-based callback or paste-back can complete
- credentials are durably stored outside transcript history
- `auth.status` can report OAuth login state
- one real OpenAI subscription-backed model call succeeds

## Proposed Narrow Contract

Add one experimental backend-owned OAuth path for one provider:

- provider id: `openai-codex`
- one login RPC:
  - `auth.login_openai_codex`
- one logout RPC is optional for the spike
- `auth.status` gains one explicit OAuth shape for this provider

Suggested status fields:

- `oauth_logged_in: bool`
- `oauth_provider: "openai-codex" | null`
- `oauth_account_label: str | null`
- `oauth_expires_at: int | null`

Do not over-generalize the contract yet.

## Storage Strategy

Use a separate backend-owned OAuth credential store for the spike.

Reason:

- existing local secret storage is built for simple provider secrets
- OAuth credentials need refresh token, access token, expiry, and optional
  account metadata
- mixing these into the secret-store seam too early will muddy the existing
  provider-secret contract

Suggested spike file:

- `~/.jaca/oauth.json`

Suggested first shape:

```json
{
  "openai-codex": {
    "type": "oauth",
    "access": "...",
    "refresh": "...",
    "expires": 1760000000000,
    "account_id": "..."
  }
}
```

This should remain backend-only until the spike proves itself.

## JACA File Targets

### Python

Likely first-touch files:

- `src/just_another_coding_agent/contracts/auth.py`
  - extend status shape for experimental OAuth login state
- `src/just_another_coding_agent/contracts/rpc.py`
  - add `auth.login_openai_codex` request/response models
- `src/just_another_coding_agent/rpc/stdio.py`
  - route login RPC
  - emit any backend-owned auth events needed by the TUI
- `src/just_another_coding_agent/runtime/models.py`
  - teach one experimental model path to use OAuth-backed auth
- `src/just_another_coding_agent/auth.py`
  - integrate OAuth credential lookup into one narrow provider path

New likely files:

- `src/just_another_coding_agent/oauth_openai_codex.py`
  - provider-specific OAuth login and refresh flow
- `src/just_another_coding_agent/oauth_store.py`
  - durable credential store for the spike

### Go TUI

Likely first-touch files:

- `internal/jaca/rpc/types.go`
  - add new auth login request/response types
- `internal/jaca/rpc/client.go`
  - add one RPC client method for the login flow
- `internal/jaca/app/auth.go`
  - focused login flow UI
- `internal/jaca/app/model.go`
  - route slash or auth action into the flow
- `internal/jaca/app/onboarding.go`
  - optionally expose the experimental path from onboarding or keep it hidden

The TUI should treat login as a focused flow, not transcript chat.

## UI Shape

Keep this very small:

1. user triggers experimental login
2. TUI shows a focused secure panel
3. backend returns URL and instructions
4. browser opens
5. user either:
   - completes local callback
   - or pastes redirect URL
6. TUI shows success or failure

No broader provider picker work is needed for the spike.

## Suggested Minimal Model Exposure

Expose exactly one experimental OAuth-backed model id for the spike.

Example direction:

- `openai-codex:gpt-5-codex`

Do not expose a full family yet.

## Test Plan

Start small and explicit.

Python:

- OAuth store round-trip test
- login response / auth status contract test
- model auth resolution test for one OAuth-backed model

Go:

- one auth-flow UI test for login panel behavior
- one RPC client test for login request/response

Manual proof:

1. start JACA
2. run `/login openai-codex`
3. finish login in browser
4. paste the redirect URL or authorization code into the login overlay
5. verify `/auth status` shows `openai-codex: logged in`
6. switch to `openai-responses:gpt-5-codex`
7. run `hello`
8. receive a real response

The spike is not done until step 7 succeeds.

## Rollout Order

1. backend-only OAuth store + provider-specific login helper
2. one `auth.login_openai_codex` RPC
3. one minimal TUI focused login flow
4. one experimental model binding to OAuth credentials
5. one real manual proof

## Exit Criteria

Proceed to real product work only if:

- login is reliable enough to repeat
- refresh works or expiry handling is explicit
- the model call succeeds end-to-end
- the backend contract still feels clean enough to generalize

If any of those fail, stop and reconsider the architecture before adding more providers.
