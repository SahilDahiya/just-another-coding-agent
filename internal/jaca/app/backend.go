package app

import (
	"context"

	"jaca/internal/jaca/rpc"
)

type Backend interface {
	SetModel(model string)
	SetEnv(env []string)
	Restart(ctx context.Context) error
	Shutdown(ctx context.Context) error
	Interrupt(ctx context.Context) error
	InterruptRun(ctx context.Context, sessionID string, promoteQueuedSteer bool) (rpc.RunInterruptResponse, error)
	CreateSession(ctx context.Context) (rpc.SessionCreateResponse, error)
	SetSessionName(ctx context.Context, sessionID string, name string) (rpc.SessionNameResponse, error)
	SessionPreview(ctx context.Context, sessionID string) (rpc.SessionPreviewResponse, error)
	CompactSession(ctx context.Context, sessionID string) (rpc.SessionCompactResponse, error)
	ModelCatalog(ctx context.Context) (rpc.ModelCatalogResponse, error)
	AuthStatus(ctx context.Context) (rpc.AuthStatusResponse, error)
	PrepareAuthFile(ctx context.Context, provider string) (rpc.AuthPrepareFileResponse, error)
	StartOpenAICodexLogin(ctx context.Context) (rpc.AuthLoginOpenAICodexStartResponse, error)
	CompleteOpenAICodexLogin(ctx context.Context, flowID string, callbackOrCode string) (rpc.AuthLoginOpenAICodexCompleteResponse, error)
	WaitOpenAICodexLogin(ctx context.Context, flowID string) (rpc.AuthLoginOpenAICodexWaitResponse, error)
	SetProviderSecret(ctx context.Context, provider string, secret string, storage string) (rpc.AuthSetResponse, error)
	ClearProviderSecret(ctx context.Context, provider string) (rpc.AuthClearResponse, error)
	EnqueueRun(ctx context.Context, sessionID string, prompt string, mode string) (rpc.RunEnqueueResponse, error)
	StreamRun(
		ctx context.Context,
		sessionID string,
		prompt string,
		thinking string,
		sink func(rpc.RunEvent) error,
	) error
}
