package market

import (
	"testing"
	"time"
)

func TestBarStruct(t *testing.T) {
	bar := Bar{
		Symbol:    "AAPL",
		Timestamp: time.Date(2026, 5, 13, 10, 0, 0, 0, time.UTC),
		Open:      189.50,
		High:      190.20,
		Low:       189.30,
		Close:     189.80,
		Volume:    1000000,
		Market:    "US",
		Frequency: "1m",
	}
	if bar.Symbol != "AAPL" {
		t.Errorf("expected AAPL, got %s", bar.Symbol)
	}
	if bar.Close != 189.80 {
		t.Errorf("expected 189.80, got %f", bar.Close)
	}
}

func TestParseMarket(t *testing.T) {
	tests := []struct {
		input    string
		expected Market
		ok       bool
	}{
		{"us", US, true},
		{"US", US, true},
		{"cn", CN, true},
		{"hk", HK, true},
		{"jp", "", false},
		{"", "", false},
	}
	for _, tc := range tests {
		m, ok := ParseMarket(tc.input)
		if ok != tc.ok || (tc.ok && m != tc.expected) {
			t.Errorf("ParseMarket(%q) = (%s, %v); want (%s, %v)", tc.input, m, ok, tc.expected, tc.ok)
		}
	}
}

func TestParseMarketCrypto(t *testing.T) {
	m, ok := ParseMarket("CRYPTO")
	if !ok {
		t.Fatal("expected CRYPTO to parse successfully")
	}
	if m != CRYPTO {
		t.Fatalf("expected CRYPTO, got %s", m)
	}
}

func TestCryptoStoragePrefix(t *testing.T) {
	prefix := CRYPTO.StoragePrefix("bars")
	expected := "raw/crypto/bars"
	if prefix != expected {
		t.Fatalf("expected %s, got %s", expected, prefix)
	}
}

func TestMarketStoragePrefix(t *testing.T) {
	tests := []struct {
		market   Market
		dataType string
		expected string
	}{
		{US, "bars", "raw/us/bars"},
		{CN, "bars", "raw/cn/bars"},
		{HK, "quotes", "raw/hk/quotes"},
	}
	for _, tc := range tests {
		got := tc.market.StoragePrefix(tc.dataType)
		if got != tc.expected {
			t.Errorf("StoragePrefix(%q) = %q; want %q", tc.dataType, got, tc.expected)
		}
	}
}
