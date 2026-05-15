package handler

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/quant/query-api/internal/reader"
)

type Handler struct {
	bucket     string
	barReader  *reader.GCSBarReader
	mux        *http.ServeMux
}

func NewHandler(bucket string) *Handler {
	h := &Handler{bucket: bucket, mux: http.NewServeMux()}
	h.mux.HandleFunc("/health", h.handleHealth)
	h.mux.HandleFunc("/api/v1/bars", h.handleBars)
	h.mux.HandleFunc("/api/v1/symbols", h.handleSymbols)
	return h
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.mux.ServeHTTP(w, r)
}

func (h *Handler) initBarReader() error {
	if h.barReader != nil {
		return nil
	}
	r, err := reader.NewGCSBarReader(h.bucket)
	if err != nil {
		return err
	}
	h.barReader = r
	return nil
}

func (h *Handler) handleHealth(w http.ResponseWriter, r *http.Request) {
	status := map[string]string{"status": "ok"}
	if err := h.initBarReader(); err != nil {
		status["gcs"] = err.Error()
	}
	writeJSON(w, http.StatusOK, status)
}

func (h *Handler) handleBars(w http.ResponseWriter, r *http.Request) {
	if err := h.initBarReader(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	q := r.URL.Query()
	params, err := reader.ParseQueryParams(
		q.Get("market"),
		q.Get("symbols"),
		q.Get("start"),
		q.Get("end"),
		q.Get("frequency"),
	)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	log.Printf("Query bars: market=%s symbols=%v start=%s end=%s freq=%s",
		params.Market, params.Symbols, params.Start, params.End, params.Frequency)

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	result, err := h.barReader.QueryBars(ctx, params)
	if err != nil {
		log.Printf("Query error: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, result)
}

func (h *Handler) handleSymbols(w http.ResponseWriter, r *http.Request) {
	mkt := strings.ToLower(r.URL.Query().Get("market"))
	if mkt == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "market is required"})
		return
	}

	if err := h.initBarReader(); err != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"symbols": []string{},
			"market":  mkt,
			"error":   err.Error(),
		})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()

	symbols, err := h.barReader.ListSymbols(ctx, mkt)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"symbols": []string{},
			"market":  mkt,
			"error":   err.Error(),
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"symbols": symbols,
		"market":  mkt,
	})
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
