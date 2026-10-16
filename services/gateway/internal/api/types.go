// Package api defines the public HTTP contract and the payload shapes exchanged with
// the Temporal workflow.
//
// These types mirror the Pydantic models in services/agent/app/core/schemas.py field
// for field. The gateway and the worker agree only by convention — a renamed JSON tag
// here surfaces as a workflow task failure in Temporal, not as a compile error — so
// keep the two files in step.
package api

import (
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Run statuses, matching app.core.schemas.RunStatus.
const (
	StatusRunning         = "running"
	StatusCompleted       = "completed"
	StatusWaitingApproval = "waiting_approval"
	StatusRejected        = "rejected"
)

// ValidationError reports a rejected field so the handler can return 400 with a
// message that tells the caller what to fix.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s %s", e.Field, e.Message)
}

func invalid(field, message string) error {
	return &ValidationError{Field: field, Message: message}
}

// checkLength enforces a rune-count range. Rune counts rather than byte lengths keep
// the limits identical to Pydantic's, which counts characters.
func checkLength(field, value string, min, max int) error {
	length := utf8.RuneCountInString(value)
	if length < min {
		if min == 1 {
			return invalid(field, "is required")
		}
		return invalid(field, fmt.Sprintf("must be at least %d characters", min))
	}
	if length > max {
		return invalid(field, fmt.Sprintf("must be at most %d characters", max))
	}
	return nil
}

func checkEnum(field, value string, allowed ...string) error {
	for _, candidate := range allowed {
		if value == candidate {
			return nil
		}
	}
	return invalid(field, "must be one of "+strings.Join(allowed, ", "))
}

// TicketRequest is the body of POST /v1/tickets.
type TicketRequest struct {
	CustomerID string         `json:"customer_id"`
	Message    string         `json:"message"`
	OrderID    *string        `json:"order_id,omitempty"`
	Channel    string         `json:"channel,omitempty"`
	Metadata   map[string]any `json:"metadata,omitempty"`
}

func (r *TicketRequest) Validate() error {
	if r.Channel == "" {
		r.Channel = "api"
	}
	if r.Metadata == nil {
		r.Metadata = map[string]any{}
	}
	if err := checkLength("customer_id", r.CustomerID, 1, 100); err != nil {
		return err
	}
	if err := checkLength("message", r.Message, 1, 8000); err != nil {
		return err
	}
	if r.OrderID != nil {
		if err := checkLength("order_id", *r.OrderID, 0, 100); err != nil {
			return err
		}
	}
	return checkEnum("channel", r.Channel, "api", "email", "chat")
}

// ApprovalDecision is the body of POST /v1/tickets/{id}/decision.
type ApprovalDecision struct {
	ReviewID string  `json:"review_id"`
	Decision string  `json:"decision"`
	Reviewer string  `json:"reviewer"`
	Comment  *string `json:"comment,omitempty"`
}

func (d *ApprovalDecision) Validate() error {
	if err := checkEnum("decision", d.Decision, "approve", "reject"); err != nil {
		return err
	}
	if err := checkLength("reviewer", d.Reviewer, 1, 100); err != nil {
		return err
	}
	if d.Comment != nil {
		if err := checkLength("comment", *d.Comment, 0, 1000); err != nil {
			return err
		}
	}
	return checkLength("review_id", d.ReviewID, 1, 200)
}

// PendingAction describes the side effect a reviewer is being asked to approve.
type PendingAction struct {
	ReviewID  string         `json:"review_id"`
	Action    string         `json:"action"`
	Arguments map[string]any `json:"arguments"`
	Reason    string         `json:"reason"`
}

// RunState is the public view of a ticket, returned by every ticket route and by the
// workflow's get_state query.
type RunState struct {
	SafetyFlags   []string       `json:"safety_flags"`
	TicketID      string         `json:"ticket_id"`
	Status        string         `json:"status"`
	Answer        *string        `json:"answer,omitempty"`
	Category      *string        `json:"category,omitempty"`
	PendingAction *PendingAction `json:"pending_action,omitempty"`
	Citations     []string       `json:"citations"`
	CreatedAt     *time.Time     `json:"created_at,omitempty"`
}

// KnowledgeDocument is one document to embed and store.
type KnowledgeDocument struct {
	ID       string         `json:"id"`
	Title    string         `json:"title"`
	Content  string         `json:"content"`
	Source   string         `json:"source"`
	Metadata map[string]any `json:"metadata,omitempty"`
}

func (d *KnowledgeDocument) Validate() error {
	if err := checkLength("id", d.ID, 1, 200); err != nil {
		return err
	}
	if err := checkLength("title", d.Title, 1, 300); err != nil {
		return err
	}
	if err := checkLength("content", d.Content, 1, 50000); err != nil {
		return err
	}
	return checkLength("source", d.Source, 1, 500)
}

// KnowledgeUpsertRequest is the body of POST /v1/knowledge.
type KnowledgeUpsertRequest struct {
	Documents []KnowledgeDocument `json:"documents"`
}

func (r *KnowledgeUpsertRequest) Validate() error {
	if len(r.Documents) == 0 {
		return invalid("documents", "is required")
	}
	if len(r.Documents) > 100 {
		return invalid("documents", "must contain at most 100 items")
	}
	for index := range r.Documents {
		if err := r.Documents[index].Validate(); err != nil {
			var validation *ValidationError
			if errors.As(err, &validation) {
				return invalid(
					fmt.Sprintf("documents[%d].%s", index, validation.Field),
					validation.Message,
				)
			}
			return err
		}
	}
	return nil
}

// KnowledgeUpsertResponse reports how many documents were written.
type KnowledgeUpsertResponse struct {
	Upserted int `json:"upserted"`
}
