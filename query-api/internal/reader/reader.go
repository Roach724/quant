package reader

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"cloud.google.com/go/storage"
)

type QueryParams struct {
	Market    string
	Symbols   []string
	Start     time.Time
	End       time.Time
	Frequency string
}

func ParseQueryParams(mkt, symbols, startStr, endStr, freq string) (QueryParams, error) {
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
	return fmt.Sprintf("raw/%s/%s/year=%04d/month=%02d/day=%02d/",
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

type GCSBarReader struct {
	bucket string
	client *storage.Client
}

func NewGCSBarReader(bucketName string) (*GCSBarReader, error) {
	ctx := context.Background()
	client, err := storage.NewClient(ctx)
	if err != nil {
		return nil, fmt.Errorf("storage client: %w", err)
	}
	return &GCSBarReader{bucket: bucketName, client: client}, nil
}

func (r *GCSBarReader) QueryBars(ctx context.Context, params QueryParams) (*QueryResult, error) {
	// List matching GCS objects for the queried symbols and date range.
	var objectURIs []string
	for _, symbol := range params.Symbols {
		d := params.Start
		for !d.After(params.End) {
			prefix := buildGCSPrefix(params.Market, "bars", d)
			target := fmt.Sprintf("symbol=%s.parquet", symbol)

			it := r.client.Bucket(r.bucket).Objects(ctx, &storage.Query{Prefix: prefix})
			for {
				attrs, err := it.Next()
				if err == io.EOF || err != nil {
					break
				}
				if strings.HasSuffix(attrs.Name, "/"+target) {
					objectURIs = append(objectURIs, fmt.Sprintf("gs://%s/%s", r.bucket, attrs.Name))
				}
			}
			d = d.AddDate(0, 0, 1)
		}
	}

	sort.Strings(objectURIs)

	return &QueryResult{
		Bars:   []BarRow{},
		Status: fmt.Sprintf("found %d matching parquet objects. Use Python SDK source=direct to read data.", len(objectURIs)),
	}, nil
}

func (r *GCSBarReader) ListSymbols(ctx context.Context, mkt string) ([]string, error) {
	prefix := fmt.Sprintf("raw/%s/bars/", strings.ToLower(mkt))
	it := r.client.Bucket(r.bucket).Objects(ctx, &storage.Query{Prefix: prefix})

	seen := make(map[string]bool)
	for {
		attrs, err := it.Next()
		if err == io.EOF || err != nil {
			break
		}
		parts := strings.Split(attrs.Name, "/")
		if len(parts) < 2 {
			continue
		}
		file := parts[len(parts)-1]
		// file is "symbol=AAPL.parquet"
		symbol := strings.TrimSuffix(strings.TrimPrefix(file, "symbol="), ".parquet")
		if symbol != "" && symbol != file {
			seen[symbol] = true
		}
	}

	symbols := make([]string, 0, len(seen))
	for s := range seen {
		symbols = append(symbols, s)
	}
	sort.Strings(symbols)
	return symbols, nil
}
