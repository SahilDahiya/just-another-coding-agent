# OAuth Authentication Flow

read_when: you want to understand JACA's OpenAI Codex OAuth flow end to end, including browser login, RPC commands, durable storage, status reporting, and runtime credential refresh

## Purpose

This doc explains the current OAuth authentication flow in JACA.

It is not a generic OAuth tutorial.

It is a map of how JACA specifically handles:

- login start
- browser callback
- manual recovery path
- durable token storage
- auth status reporting
- credential refresh
- model use at runtime

## Read This With

This doc builds on:

- [05-identity-api-and-observability.md](05-identity-api-and-observability.md)
- [../chatgpt-subscription-oauth-spike.md](../chatgpt-subscription-oauth-spike.md)
- [../contracts.md](../contracts.md)

And the main code anchors are:

- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)
- [../../src/just_another_coding_agent/contracts/rpc.py](../../src/just_another_coding_agent/contracts/rpc.py)
- [../../src/just_another_coding_agent/auth.py](../../src/just_another_coding_agent/auth.py)
- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py)
- [../../src/just_another_coding_agent/oauth_store.py](../../src/just_another_coding_agent/oauth_store.py)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py)
- [../../src/just_another_coding_agent/runtime/models.py](../../src/just_another_coding_agent/runtime/models.py)

## What Problem This Flow Solves

JACA supports normal API-key auth for providers like OpenAI and Anthropic.

This OAuth flow exists for a narrower case:

- OpenAI Codex / ChatGPT subscription-backed access

The goal is:

- let the user log in through a browser
- persist refreshable credentials outside transcript history
- report status through the backend contract
- use those credentials later to build the OpenAI provider at runtime

## High-Level Flow

At a high level, the flow is:

```text
client starts OAuth login
    |
    v
backend creates login flow state + auth URL
    |
    v
browser login happens with PKCE
    |
    +--> local callback path succeeds automatically
    |
    +--> or manual pasted code / callback URL completes it
    |
    v
credentials persisted to ~/.jaca/oauth.json
    |
    v
auth.status reports openai-codex logged in
    |
    v
runtime resolves credentials when building OAuth-backed model provider
    |
    +--> use as-is if still valid
    |
    +--> refresh if expired
```

## The Main Components

### 1. Auth Status Contract

The public auth contract includes:

- `OAuthProviderStatus`

See:

- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)

This gives the backend a stable way to report:

- which OAuth provider this is
- whether the user is logged in
- account id
- expiry time

### 2. OAuth Protocol Implementation

The provider-specific protocol logic lives in:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py)

This module owns:

- PKCE verifier/challenge generation
- authorize URL construction
- local callback server
- authorization-code exchange
- refresh-token exchange
- extracting account id from the returned access token

### 3. Unified Auth Facade

The higher-level public auth API lives in:

- [../../src/just_another_coding_agent/auth.py](../../src/just_another_coding_agent/auth.py)

This module owns:

- starting the flow
- completing the flow
- waiting for callback completion
- storing credentials
- returning `OAuthProviderStatus`
- resolving and refreshing credentials later

### 4. Durable OAuth Store

Persisted OAuth credentials live in:

- [../../src/just_another_coding_agent/oauth_store.py](../../src/just_another_coding_agent/oauth_store.py)

Current store path:

- `~/.jaca/oauth.json`

The main stored shape is:

- access token
- refresh token
- expiry timestamp
- account id

### 5. RPC Layer

The external login flow is exposed through:

- [../../src/just_another_coding_agent/contracts/rpc.py](../../src/just_another_coding_agent/contracts/rpc.py)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py)

The key RPC commands are:

- `auth.login_openai_codex.start`
- `auth.login_openai_codex.wait`
- `auth.login_openai_codex.complete`

## The Flow Step By Step

## Step 1: Client Starts Login

The client calls:

- `auth.login_openai_codex.start`

The RPC request model is defined in:

- [../../src/just_another_coding_agent/contracts/rpc.py](../../src/just_another_coding_agent/contracts/rpc.py:139)

The stdio handler is:

- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py:709)

What happens:

1. backend creates a new OAuth flow with:
   - `flow_id`
   - PKCE verifier
   - state token
   - authorize URL
2. backend cancels any older in-flight OpenAI Codex login flow
3. backend starts a background wait task for automatic browser callback completion
4. backend returns:
   - `flow_id`
   - `auth_url`
   - user instructions

This means the backend owns the flow state, not the client.

That is an important design choice.

## Step 2: Browser Login Uses PKCE

The login start helper is:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:41)

It creates:

- a PKCE verifier
- a PKCE challenge
- an OAuth `state`
- a random `flow_id`

The authorize URL includes:

- `response_type=code`
- `client_id`
- `redirect_uri`
- `scope`
- `code_challenge`
- `code_challenge_method=S256`
- `state`

See:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:255)

That means this is a proper authorization-code flow with PKCE, not a weaker ad hoc login shim.

## PKCE In Plain English

PKCE stands for:

- Proof Key for Code Exchange

The problem it solves is:

- after the browser login succeeds, the OAuth server returns an authorization code
- that code is valuable
- if some other party steals that code before the real app exchanges it, they may be able to trade it for tokens

PKCE reduces that risk by making the authorization code useful only to the client that proves it knows a secret value created at login start.

In plain English:

```text
JACA creates a secret random string before the browser opens.
JACA sends only a hashed form of that string in the authorize request.
Later, when exchanging the returned code for tokens, JACA sends the original secret string.
The OAuth server checks that the original string matches the earlier hash.
If it does not match, the token exchange fails.
```

### The Three Important Values

1. `verifier`
- random secret generated by JACA
- never sent in the initial browser authorize request

2. `challenge`
- derived from the verifier using SHA-256 and base64url encoding
- sent in the authorize request

3. `state`
- separate random anti-CSRF token
- not the same thing as PKCE
- used to make sure the callback belongs to the login flow JACA started

### How JACA Implements It

The PKCE pieces live in:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:274)

Relevant functions:

- `_create_pkce_verifier(...)`
- `_create_pkce_challenge(...)`

At login start:

1. JACA generates a verifier.
2. JACA derives the challenge from that verifier.
3. JACA sends the challenge in the browser authorize URL.
4. JACA keeps the verifier in the in-memory login flow state.

Then during token exchange:

- JACA sends the original verifier back to the token endpoint

See:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:304)

where the token exchange includes:

- `grant_type=authorization_code`
- `client_id`
- `code`
- `code_verifier`
- `redirect_uri`

### Why PKCE Matters Here

JACA is not a confidential backend-only web app with some private server secret embedded in a browser flow.

It is a local agent product that:

- opens a browser
- receives a callback on localhost
- may also accept manual pasted callback data

That makes PKCE the right shape for this OAuth flow.

### PKCE Vs State

Do not confuse these:

- PKCE protects the code exchange by binding the authorization code to the client that started the flow
- `state` protects against callback mixups and CSRF-style flow confusion

JACA uses both.

### One-Line Interview Explanation

If asked what PKCE is, give this answer:

> PKCE is a way to bind the returned OAuth authorization code to the client that initiated the login flow. JACA generates a verifier, sends only its derived challenge in the browser request, and later proves possession of the verifier when exchanging the code for tokens. That way, stealing the raw authorization code alone is not enough.

## Step 3: Automatic Browser Callback Path

The canonical path is:

- browser redirects to local callback
- backend callback server catches it
- backend exchanges the authorization code for tokens
- backend persists credentials
- waiting RPC returns success

The callback server logic lives in:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:136)

Important details:

- callback host is `localhost`
- callback port is `1455`
- callback path is `/auth/callback`
- state must match
- missing code is rejected

This means the backend can complete login without the user manually copying anything if loopback callback works.

## Step 4: Manual Recovery Path

If automatic callback does not complete, the fallback path is:

- client calls `auth.login_openai_codex.complete`
- user provides pasted redirect URL or raw code

The request includes:

- `flow_id`
- `callback_or_code`

The handler is:

- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py:779)

The parser accepts:

- full callback URL
- query string with `code=...`
- raw authorization code

See:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:283)

This is important because the manual path is recovery, not a second independent protocol.

Both automatic and manual completion resolve the same canonical login result.

That is also stated in the contracts doc:

- [../contracts.md](../contracts.md:102)

## Step 5: Token Exchange

Once JACA has the authorization code, it exchanges it for tokens at:

- `https://auth.openai.com/oauth/token`

See:

- [../../src/just_another_coding_agent/oauth_openai_codex.py](../../src/just_another_coding_agent/oauth_openai_codex.py:304)

The returned payload must include:

- `access_token`
- `refresh_token`
- `expires_in`

JACA then derives:

- `expires`
- `account_id`

The account id is extracted from the access token claims.

## Step 6: Credential Persistence

After successful completion, credentials are stored in:

- `~/.jaca/oauth.json`

Persistence happens through:

- `set_openai_codex_credentials(...)`

in:

- [../../src/just_another_coding_agent/oauth_store.py](../../src/just_another_coding_agent/oauth_store.py:76)

The write behavior is careful:

- ensure directory exists
- write to temp file
- chmod `0600`
- replace target atomically

That is good credential-store hygiene.

## Step 7: Auth Status Reporting

After persistence, JACA can report OAuth status through:

- `get_oauth_provider_statuses()`

in:

- [../../src/just_another_coding_agent/auth.py](../../src/just_another_coding_agent/auth.py:127)

And that is surfaced through:

- `auth.status`

via the RPC layer in `stdio.py`.

So the contract-visible auth state is:

- backend owned
- not inferred by the client
- durable across process restarts because the store is on disk

## Step 8: Runtime Credential Resolution

Later, when the runtime needs an OAuth-backed model, it resolves credentials through:

- `resolve_openai_codex_oauth_credentials_sync()`

in:

- [../../src/just_another_coding_agent/auth.py](../../src/just_another_coding_agent/auth.py:210)

Resolution order is:

1. explicit environment variables, if present
2. otherwise stored credentials from `~/.jaca/oauth.json`

If credentials are expired:

- refresh them
- persist refreshed credentials back to disk unless env credentials were used

This is a good design choice because:

- env injection can override local store for controlled environments
- local interactive use still works durably

## Step 9: Runtime Model Construction

The OAuth-backed provider path is used when building certain OpenAI responses models.

See:

- [../../src/just_another_coding_agent/runtime/models.py](../../src/just_another_coding_agent/runtime/models.py:127)

The provider is built with:

- base URL for the Codex path
- OAuth access token as API key
- special headers including:
  - `chatgpt-account-id`
  - `originator: jaca`
  - `OpenAI-Beta: responses=experimental`

This is the final point where OAuth credentials become model runtime access.

## Visual Flow

```text
client
  |
  +--> auth.login_openai_codex.start
          |
          v
     backend creates flow_id + PKCE + state + auth_url
          |
          +--> background wait task started
          |
          v
     browser login
          |
          +--> local callback to localhost:1455/auth/callback
          |       |
          |       v
          |   code exchange -> credentials -> oauth.json
          |
          +--> or manual paste into auth.login_openai_codex.complete
                  |
                  v
              code exchange -> credentials -> oauth.json
                                      |
                                      v
                               auth.status shows logged_in
                                      |
                                      v
                       runtime resolves / refreshes credentials
                                      |
                                      v
                           OpenAI Codex provider built for model
```

## Invariants

These are the important invariants.

1. The backend owns the OAuth login state, not the client.
2. Manual completion and automatic browser callback must resolve the same canonical result.
3. Credentials must be stored outside transcript history.
4. Auth status must be explicit contract data.
5. Expired credentials must refresh or fail clearly.
6. OAuth store writes should be durable and not leave half-written credential state behind.

## What Is Replaceable

Replaceable:

- stdio as the RPC transport
- the client UI flow that drives login
- the exact callback presentation page
- storage backend implementation details

Not replaceable without changing the design:

- backend-owned auth semantics
- explicit start / wait / complete lifecycle
- persisted refreshable credential store
- runtime credential resolution before model construction

## Tradeoffs

### Good Tradeoff

JACA keeps OAuth provider-specific and narrow.

That is good because:

- the boundary stays understandable
- the contract is not over-generalized too early
- one provider path can be proven end to end first

### Cost

This is not yet a generic enterprise identity framework.

That is fine.

It is an intentionally narrow, backend-owned OAuth path.

## How To Explain It In Interview Language

Good answer:

> JACA treats OAuth as backend-owned auth lifecycle, not as a frontend convenience flow. The backend creates and tracks a login flow, exposes explicit start, wait, and manual-complete RPC commands, persists refreshable credentials in a dedicated OAuth store, reports auth state through contract models, and later resolves or refreshes those credentials when constructing the provider used by the runtime.

## Good Pushback To Practice

1. Why not let the client own the browser callback flow entirely?
2. Why separate OAuth store from the normal provider secret store?
3. Why do you need both `wait` and `complete` RPC commands?
4. Why should auth status be contract-visible instead of derived implicitly from login success?
5. What should happen if refresh fails after credentials have been persisted?

## Relationship To The Spike Doc

The spike doc is the design intention:

- [../chatgpt-subscription-oauth-spike.md](../chatgpt-subscription-oauth-spike.md)

This doc is the implementation map.

Use them together like this:

- spike doc tells you why the boundary was chosen
- this doc tells you how the current flow actually works
