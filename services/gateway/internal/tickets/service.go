// Package tickets drives the durable support workflow.
//
// The HTTP handler depends on the Service interface rather than on a Temporal client,
// so handler tests need no Temporal server.
package tickets

import (
	"context"
	"errors"

	"github.com/example/support-agent/services/gateway/internal/api"
)

var (
	ErrStaleReview = errors.New("review id does not match the pending review")
	// ErrNotFound means no workflow exists for the ticket id.
	ErrNotFound = errors.New("ticket not found")
	// ErrNotAwaiting means the ticket exists but is not paused for review.
	ErrNotAwaiting = errors.New("ticket is not awaiting review")
)

// Service is the gateway's view of the agent runtime.
type Service interface {
	// Start begins a durable run and returns immediately; it does not wait for the
	// agent to finish reasoning.
	Start(ctx context.Context, request api.TicketRequest) (api.RunState, error)
	// Get reads the current state of a run, whether it is in flight or closed.
	Get(ctx context.Context, ticketID string) (api.RunState, error)
	// Decide delivers a reviewer's verdict to a paused run.
	Decide(ctx context.Context, ticketID string, decision api.ApprovalDecision) (api.RunState, error)
}
