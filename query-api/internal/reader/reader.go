package reader

import (
	"fmt"
	"strings"
	"time"

	"github.com/quant/query-api/internal/market"
)

type QueryParams struct {
	Market    string
	Symbols   []string
	Start     time.Time
	End       time.Time
	Frequency string
}

func ParseQueryParams(mkt, symbols, startStr, endStr, freq string) (QueryParams, error) {
	if _, ok := market.ParseMarket(mkt); !ok {
		return QueryParams{}, fmt.Errorf("invalid market: %s", mkt)
	}
	start, err := time.Parse(time.RFC3339, startStr)
	if err != nil {
		return QueryParams{}, fmt.Errorf("invalid start time: %w", err)
	}
	end, err := time.Parse(time.RFC3339, endStr)
	if err != nil {
		return QueryParams{}, fmt.Errorf("invalid end time: %w", err)
	}
	if freq == "" {
		freq = "1m"
	}
	return QueryParams{
		Market:    strings.ToLower(mkt),
		Symbols:   splitTrim(symbols),
		Start:     start,
		End:       end,
		Frequency: freq,
	}, nil
}

func buildGCSPrefix(mkt, dataType string, date time.Time) string {
	return fmt.Sprintf("raw/%s/%s/%04d/%02d/%02d/",
		strings.ToLower(mkt), dataType,
		date.Year(), date.Month(), date.Day())
}

func splitTrim(s string) []string {
	parts := strings.Split(s, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		trimmed := strings.TrimSpace(p)
		if trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

type BarRow struct {
	Symbol    string    `json:"symbol"`
	Timestamp time.Time `json:"timestamp"`
	Open      float64   `json:"open"`
	High      float64   `json:"high"`
	Low       float64   `json:"low"`
	Close     float64   `json:"close"`
	Volume    int64     `json:"volume"`
	Market    string    `json:"market"`
	Frequency string    `json:"frequency"`
}

type QueryResult struct {
	Bars   []BarRow `json:"bars"`
	Status string   `json:"status"`
}
