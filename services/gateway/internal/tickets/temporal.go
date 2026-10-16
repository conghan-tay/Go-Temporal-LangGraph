package tickets

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/sdk/client"

	"github.com/example/support-agent/services/gateway/internal/api"
)

// These names are the cross-language contract with the Python worker. They must match
// the @workflow.defn / @workflow.signal / @workflow.query names declared in
// services/agent/app/temporal/workflows.py.
const (
	workflowSupportTicket = "SupportTicketWorkflow"
	signalSubmitDecision  = "submit_decision"
	queryGetState         = "get_state"
)

// ticketIDPrefix keeps generated workflow ids recognisable in the Temporal UI.
const ticketIDPrefix = "ticket-"

// TemporalService starts and inspects support workflows.
type TemporalService struct {
	client               client.Client
	taskQueue            string
	approvalTimeoutHours int
	executionTimeout     time.Duration
}

// NewTemporalService wires the gateway to a Temporal namespace.
//
// approvalTimeoutHours is passed into each workflow: workflow code cannot read the
// environment, so the deadline travels as an explicit argument. 0 waits forever.
func NewTemporalService(
	temporalClient client.Client, taskQueue string, approvalTimeoutHours int,
) *TemporalService {
	// Give a run two approval windows (input review and business action), plus headroom, so a
	// forgotten ticket is eventually closed out by Temporal even if the deadline logic
	// is bypassed.
	executionTimeout := 2*time.Duration(approvalTimeoutHours)*time.Hour + time.Hour
	if approvalTimeoutHours == 0 {
		executionTimeout = 0 // unlimited, matching the "wait forever" policy
	}
	return &TemporalService{
		client:               temporalClient,
		taskQueue:            taskQueue,
		approvalTimeoutHours: approvalTimeoutHours,
		executionTimeout:     executionTimeout,
	}
}

func (s *TemporalService) Start(
	ctx context.Context, request api.TicketRequest,
) (api.RunState, error) {
	ticketID := ticketIDPrefix + uuid.NewString()
	options := client.StartWorkflowOptions{
		ID:        ticketID,
		TaskQueue: s.taskQueue,
		// The gateway mints the id, so a duplicate can only be a bug or a replayed
		// request; refuse it rather than silently attaching to another run.
		WorkflowIDReusePolicy:    enums.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE,
		WorkflowExecutionTimeout: s.executionTimeout,
	}
	_, err := s.client.ExecuteWorkflow(
		ctx, options, workflowSupportTicket, request, s.approvalTimeoutHours,
	)
	if err != nil {
		return api.RunState{}, fmt.Errorf("start support workflow: %w", err)
	}
	return api.RunState{
		TicketID:    ticketID,
		Status:      api.StatusRunning,
		Citations:   []string{},
		SafetyFlags: []string{},
	}, nil
}

func (s *TemporalService) Get(ctx context.Context, ticketID string) (api.RunState, error) {
	encoded, err := s.client.QueryWorkflow(ctx, ticketID, "", queryGetState)
	if err != nil {
		return api.RunState{}, translateQueryError(err)
	}
	var state api.RunState
	if err := encoded.Get(&state); err != nil {
		return api.RunState{}, fmt.Errorf("decode run state: %w", err)
	}
	// The workflow knows its own id, but trust the routed one so a response can never
	// disagree with the URL that produced it.
	state.TicketID = ticketID
	if state.SafetyFlags == nil {
		state.SafetyFlags = []string{}
	}
	if state.Citations == nil {
		state.Citations = []string{}
	}
	return state, nil
}

func (s *TemporalService) Decide(
	ctx context.Context, ticketID string, decision api.ApprovalDecision,
) (api.RunState, error) {
	// Read first so the caller gets a real 404/409 instead of a silent no-op. Signals
	// are fire-and-forget, so this check can race; the workflow's signal handler is the
	// authority and drops a decision that arrives outside the pause.
	state, err := s.Get(ctx, ticketID)
	if err != nil {
		return api.RunState{}, err
	}
	if state.Status != api.StatusWaitingApproval {
		return api.RunState{}, ErrNotAwaiting
	}
	if state.PendingAction == nil || decision.ReviewID != state.PendingAction.ReviewID {
		return api.RunState{}, ErrStaleReview
	}
	if err := s.client.SignalWorkflow(
		ctx, ticketID, "", signalSubmitDecision, decision,
	); err != nil {
		return api.RunState{}, fmt.Errorf("signal decision: %w", err)
	}
	return api.RunState{
		TicketID:    ticketID,
		Status:      api.StatusRunning,
		Citations:   []string{},
		SafetyFlags: []string{},
	}, nil
}

// translateQueryError maps Temporal's transport errors onto the package's sentinels so
// the HTTP layer never imports Temporal.
func translateQueryError(err error) error {
	var notFound *serviceerror.NotFound
	if errors.As(err, &notFound) {
		return ErrNotFound
	}
	return fmt.Errorf("query run state: %w", err)
}
