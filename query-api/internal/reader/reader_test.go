package reader

import (
	"testing"
	"time"
)

func TestBuildGCSPrefix(t *testing.T) {
	tests := []struct {
		market   string
		dataType string
		freq     string
		date     time.Time
		expected string
	}{
		{"us", "bars", "1m", time.Date(2026, 5, 13, 0, 0, 0, 0, time.UTC), "raw/us/bars/freq=1m/year=2026/month=05/day=13/"},
		{"cn", "bars", "1d", time.Date(2026, 1, 5, 0, 0, 0, 0, time.UTC), "raw/cn/bars/freq=1d/year=2026/month=01/day=05/"},
	}
	for _, tc := range tests {
		got := buildGCSPrefix(tc.market, tc.dataType, tc.freq, tc.date)
		if got != tc.expected {
			t.Errorf("buildGCSPrefix(%q, %q, %q, %v) = %q; want %q",
				tc.market, tc.dataType, tc.freq, tc.date, got, tc.expected)
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
