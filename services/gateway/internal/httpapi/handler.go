package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"

	"github.com/example/support-agent/services/gateway/internal/api"
	"github.com/example/support-agent/services/gateway/internal/knowledge"
	"github.com/example/support-agent/services/gateway/internal/tickets"
)

const maxRequestBytes = 1 << 20

// HealthChecker reports whether the agent runtime is reachable. Implemented by the
// Temporal client; kept as an interface so tests need no Temporal server.
type HealthChecker interface {
	CheckHealth(ctx context.Context) error
}

// Handler is the public API. It owns authentication, rate limiting, validation, and
// HTTP semantics; the agent runtime is reached only through the tickets and knowledge
// interfaces.
type Handler struct {
	apiKey    string
	tickets   tickets.Service
	knowledge knowledge.Repository
	health    HealthChecker
	limiter   RateLimiter
	logger    *slog.Logger
	timeout   time.Duration
}

// Options bundles the handler's collaborators; there are enough of them now that
// positional parameters would be easy to transpose.
type Options struct {
	APIKey    string
	Tickets   tickets.Service
	Knowledge knowledge.Repository
	Health    HealthChecker
	Limiter   RateLimiter
	Logger    *slog.Logger
	Timeout   time.Duration
}

func New(options Options) http.Handler {
	if options.Limiter == nil {
		options.Limiter = allowAllLimiter{}
	}
	h := &Handler{
		apiKey:    options.APIKey,
		tickets:   options.Tickets,
		knowledge: options.Knowledge,
		health:    options.Health,
		limiter:   options.Limiter,
		logger:    options.Logger,
		timeout:   options.Timeout,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.healthz)
	mux.HandleFunc("GET /readyz", h.readyz)
	mux.HandleFunc("POST /v1/tickets", h.createTicket)
	mux.HandleFunc("GET /v1/tickets/{ticketID}", h.getTicket)
	mux.HandleFunc("POST /v1/tickets/{ticketID}/decision", h.decideTicket)
	mux.HandleFunc("POST /v1/knowledge", h.upsertKnowledge)
	return h.requestContext(h.authenticate(mux))
}

// healthz is liveness only: it must not depend on Temporal, or a Temporal blip would
// have orchestrators restarting healthy gateways.
func (h *Handler) healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": "gateway"})
}

// readyz reports whether this gateway can currently reach the agent runtime.
func (h *Handler) readyz(w http.ResponseWriter, r *http.Request) {
	if h.health == nil {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	if err := h.health.CheckHealth(ctx); err != nil {
		h.logger.Warn("readiness check failed", "error", err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"status": "unavailable", "detail": "agent runtime unreachable",
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// createTicket starts a durable run and returns immediately. The agent may take tens of
// seconds and may pause for a human, so the run is polled through getTicket rather than
// held open on this request.
func (h *Handler) createTicket(w http.ResponseWriter, r *http.Request) {
	var request api.TicketRequest
	if !h.decode(w, r, &request) {
		return
	}
	if err := request.Validate(); err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), h.timeout)
	defer cancel()

	state, err := h.tickets.Start(ctx, request)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	h.logCompleted(r, http.StatusAccepted, state.TicketID)
	writeJSON(w, http.StatusAccepted, state)
}

func (h *Handler) getTicket(w http.ResponseWriter, r *http.Request) {
	ticketID := r.PathValue("ticketID")
	if !validPathID(ticketID) {
		writeError(w, http.StatusBadRequest, "invalid ticket id")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), h.timeout)
	defer cancel()

	state, err := h.tickets.Get(ctx, ticketID)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	h.logCompleted(r, http.StatusOK, ticketID)
	writeJSON(w, http.StatusOK, state)
}

func (h *Handler) decideTicket(w http.ResponseWriter, r *http.Request) {
	ticketID := r.PathValue("ticketID")
	if !validPathID(ticketID) {
		writeError(w, http.StatusBadRequest, "invalid ticket id")
		return
	}
	var decision api.ApprovalDecision
	if !h.decode(w, r, &decision) {
		return
	}
	if err := decision.Validate(); err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), h.timeout)
	defer cancel()

	state, err := h.tickets.Decide(ctx, ticketID, decision)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	h.logCompleted(r, http.StatusAccepted, ticketID)
	writeJSON(w, http.StatusAccepted, state)
}

// upsertKnowledge writes straight to the vector store. It stays synchronous because
// seeding is a short, idempotent operation whose result the caller wants.
func (h *Handler) upsertKnowledge(w http.ResponseWriter, r *http.Request) {
	var request api.KnowledgeUpsertRequest
	if !h.decode(w, r, &request) {
		return
	}
	if err := request.Validate(); err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), h.timeout)
	defer cancel()

	upserted, err := h.knowledge.Upsert(ctx, request.Documents)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	h.logCompleted(r, http.StatusOK, "")
	writeJSON(w, http.StatusOK, api.KnowledgeUpsertResponse{Upserted: upserted})
}

// decode reads and unmarshals a JSON body, writing the error response itself. It
// returns false when the caller should stop.
func (h *Handler) decode(w http.ResponseWriter, r *http.Request, target any) bool {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		writeError(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json")
		return false
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxRequestBytes))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "request body is too large")
		return false
	}
	if err := json.Unmarshal(body, target); err != nil {
		writeError(w, http.StatusBadRequest, "request body must be valid JSON")
		return false
	}
	return true
}

// writeServiceError is the single place that decides an HTTP status for a failure, so
// the API cannot drift between routes.
func (h *Handler) writeServiceError(w http.ResponseWriter, r *http.Request, err error) {
	var validation *api.ValidationError
	switch {
	case errors.As(err, &validation):
		writeError(w, http.StatusBadRequest, validation.Error())
	case errors.Is(err, tickets.ErrNotFound):
		writeError(w, http.StatusNotFound, "ticket not found")
	case errors.Is(err, tickets.ErrStaleReview):
		writeError(w, http.StatusConflict, "review id does not match the pending review")
	case errors.Is(err, tickets.ErrNotAwaiting):
		writeError(w, http.StatusConflict, "ticket is not awaiting review")
	case errors.Is(err, context.DeadlineExceeded):
		h.logFailure(r, err)
		writeError(w, http.StatusGatewayTimeout, "the agent runtime did not respond in time")
	default:
		h.logFailure(r, err)
		writeError(w, http.StatusBadGateway, "agent runtime unavailable")
	}
}

func (h *Handler) logFailure(r *http.Request, err error) {
	h.logger.Error(
		"gateway request failed",
		"error", err,
		"request_id", requestIDFrom(r.Context()),
		"method", r.Method,
		"path", r.URL.Path,
	)
}

func (h *Handler) logCompleted(r *http.Request, status int, ticketID string) {
	h.logger.Info(
		"gateway request completed",
		"request_id", requestIDFrom(r.Context()),
		"method", r.Method,
		"path", r.URL.Path,
		"status", status,
		"ticket_id", ticketID,
	)
}

func (h *Handler) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
			next.ServeHTTP(w, r)
			return
		}
		allowed, err := h.limiter.Allow(r.Context(), rateLimitKey(r))
		if err != nil {
			// Rate limiting fails open so a Redis outage does not take down support.
			h.logger.Warn("rate limiter unavailable", "error", err)
		} else if !allowed {
			writeError(w, http.StatusTooManyRequests, "rate limit exceeded")
			return
		}
		provided := r.Header.Get("X-API-Key")
		if provided == "" {
			provided = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		}
		if subtle.ConstantTimeCompare([]byte(provided), []byte(h.apiKey)) != 1 {
			writeError(w, http.StatusUnauthorized, "invalid API key")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func rateLimitKey(r *http.Request) string {
	if forwarded := strings.TrimSpace(strings.Split(r.Header.Get("X-Forwarded-For"), ",")[0]); forwarded != "" {
		if addr, err := netip.ParseAddr(forwarded); err == nil {
			return addr.String()
		}
	}
	// net.SplitHostPort rather than a cut at the first colon: RemoteAddr for an IPv6
	// client is "[2001:db8::1]:54321", and cutting there yields "[", collapsing every
	// IPv6 caller in the world into a single shared bucket.
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return r.RemoteAddr
}

type contextKey string

const requestIDKey contextKey = "request-id"

func (h *Handler) requestContext(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := r.Header.Get("X-Request-ID")
		if requestID == "" || len(requestID) > 100 {
			requestID = fmt.Sprintf("req-%d", time.Now().UnixNano())
		}
		w.Header().Set("X-Request-ID", requestID)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), requestIDKey, requestID)))
	})
}

func requestIDFrom(ctx context.Context) string {
	value, _ := ctx.Value(requestIDKey).(string)
	return value
}

func validPathID(value string) bool {
	return value != "" && len(value) <= 100 && !strings.ContainsAny(value, "/\\")
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	body, err := json.Marshal(payload)
	if err != nil {
		// Every payload here is a plain struct or map of strings, so this is
		// unreachable short of a programming error.
		http.Error(w, `{"error":"internal error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
