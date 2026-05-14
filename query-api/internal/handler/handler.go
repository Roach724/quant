package handler

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"

	"github.com/quant/query-api/internal/reader"
)

type Handler struct {
	bucket string
	mux    *http.ServeMux
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

func (h *Handler) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *Handler) handleBars(w http.ResponseWriter, r *http.Request) {
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

	result := reader.QueryResult{
		Bars:   []reader.BarRow{},
		Status: "ok",
	}
	writeJSON(w, http.StatusOK, result)
}

func (h *Handler) handleSymbols(w http.ResponseWriter, r *http.Request) {
	market := strings.ToLower(r.URL.Query().Get("market"))
	if market == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "market is required"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"symbols": []string{},
		"market":  market,
	})
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
