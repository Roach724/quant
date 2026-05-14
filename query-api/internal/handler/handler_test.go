package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthEndpoint(t *testing.T) {
	h := NewHandler("test-bucket")
	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()

	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var body map[string]string
	json.NewDecoder(w.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Errorf("expected status ok, got %s", body["status"])
	}
}

func TestBarsEndpointMissingParams(t *testing.T) {
	h := NewHandler("test-bucket")
	req := httptest.NewRequest("GET", "/api/v1/bars", nil)
	w := httptest.NewRecorder()

	h.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestBarsEndpointInvalidMarket(t *testing.T) {
	h := NewHandler("test-bucket")
	req := httptest.NewRequest("GET", "/api/v1/bars?market=jp&symbols=AAPL&start=2026-05-01T00:00:00Z&end=2026-05-13T23:59:59Z", nil)
	w := httptest.NewRecorder()

	h.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid market, got %d", w.Code)
	}
}

func TestSymbolsEndpoint(t *testing.T) {
	h := NewHandler("test-bucket")
	req := httptest.NewRequest("GET", "/api/v1/symbols?market=us", nil)
	w := httptest.NewRecorder()

	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}
