package tickets

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/example/support-agent/services/gateway/internal/api"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/converter"
)

type encodedState struct{ state api.RunState }

func (e encodedState) HasValue() bool { return true }
func (e encodedState) Get(target any) error {
	data, err := json.Marshal(e.state)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}

type reviewClient struct {
	client.Client
	state    api.RunState
	signals  int
	decision api.ApprovalDecision
}

func (c *reviewClient) QueryWorkflow(context.Context, string, string, string, ...any) (converter.EncodedValue, error) {
	return encodedState{c.state}, nil
}
func (c *reviewClient) SignalWorkflow(_ context.Context, _, _, _ string, arg any) error {
	c.signals++
	c.decision = arg.(api.ApprovalDecision)
	return nil
}
func TestDecisionCorrelatesPendingReview(t *testing.T) {
	for _, id := range []string{"ticket:input_review", "ticket:business_action"} {
		t.Run(id, func(t *testing.T) {
			c := &reviewClient{state: api.RunState{
				Status:        api.StatusWaitingApproval,
				PendingAction: &api.PendingAction{ReviewID: "ticket:business_action"},
			}}
			s := NewTemporalService(c, "queue", 72)
			_, err := s.Decide(context.Background(), "ticket", api.ApprovalDecision{
				ReviewID: id, Decision: "approve", Reviewer: "manager",
			})
			if id == "ticket:input_review" {
				if !errors.Is(err, ErrStaleReview) || c.signals != 0 {
					t.Fatalf("err=%v signals=%d", err, c.signals)
				}
			} else if err != nil || c.signals != 1 || c.decision.ReviewID != id {
				t.Fatalf("err=%v signals=%d", err, c.signals)
			}
		})
	}
}
func TestExecutionTimeoutAccommodatesBothReviews(t *testing.T) {
	if got := NewTemporalService(nil, "queue", 72).executionTimeout; got != 145*time.Hour {
		t.Fatalf("timeout=%v", got)
	}
	if got := NewTemporalService(nil, "queue", 0).executionTimeout; got != 0 {
		t.Fatalf("timeout=%v", got)
	}
}
