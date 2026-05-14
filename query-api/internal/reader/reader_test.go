package reader

import (
	"testing"
	"time"
)

func TestBuildGCSPrefix(t *testing.T) {
	tests := []struct {
		market   string
		dataType string
		date     time.Time
		expected string
	}{
		{"us", "bars", time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC), "raw/us/bars/2026/05/13/"},
		{"cn", "bars", time.Date(2026, 1, 5, 0, 0, 0, 0, time.UTC), "raw/cn/bars/2026/01/05/"},
	}
	for _, tc := range tests {
		got := buildGCSPrefix(tc.market, tc.dataType, tc.date)
		if got != tc.expected {
			t.Errorf("buildGCSPrefix(%q, %q, %v) = %q; want %q",
				tc.market, tc.dataType, tc.date, got, tc.expected)
		}
	}
}

func TestParseParams(t *testing.T) {
	params, err := ParseQueryParams(
		"us", "AAPL,MSFT",
		"2026-05-01T00:00:00Z", "2026-05-13T23:59:59Z",
		"1m",
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if params.Market != "us" {
		t.Errorf("expected market us, got %s", params.Market)
	}
	if len(params.Symbols) != 2 {
		t.Errorf("expected 2 symbols, got %d", len(params.Symbols))
	}
	if params.Frequency != "1m" {
		t.Errorf("expected frequency 1m, got %s", params.Frequency)
	}
}

func TestParseParamsInvalidMarket(t *testing.T) {
	_, err := ParseQueryParams(
		"jp", "AAPL",
		"2026-05-01T00:00:00Z", "2026-05-13T23:59:59Z",
		"1m",
	)
	if err == nil {
		t.Error("expected error for invalid market")
	}
}
