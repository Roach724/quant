package reader

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"cloud.google.com/go/storage"

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

func buildGCSPrefix(mkt, dataType, freq string, date time.Time) string {
	return fmt.Sprintf("raw/%s/%s/freq=%s/year=%04d/month=%02d/day=%02d/",
		strings.ToLower(mkt), dataType, freq,
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
	var objectURIs []string
	for _, symbol := range params.Symbols {
		d := params.Start
		for !d.After(params.End) {
			prefix := buildGCSPrefix(params.Market, "bars", params.Frequency, d)
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

	var allBars []BarRow
	for _, uri := range objectURIs {
		objName := strings.TrimPrefix(uri, fmt.Sprintf("gs://%s/", r.bucket))
		bars, err := r.readBarData(ctx, objName)
		if err != nil {
			continue
		}
		for _, bar := range bars {
			if (bar.Timestamp.Equal(params.Start) || bar.Timestamp.After(params.Start)) &&
				(bar.Timestamp.Equal(params.End) || bar.Timestamp.Before(params.End)) {
				allBars = append(allBars, bar)
			}
		}
	}

	sort.Slice(allBars, func(i, j int) bool {
		if allBars[i].Symbol != allBars[j].Symbol {
			return allBars[i].Symbol < allBars[j].Symbol
		}
		return allBars[i].Timestamp.Before(allBars[j].Timestamp)
	})

	return &QueryResult{
		Bars:   allBars,
		Status: fmt.Sprintf("found %d objects, returned %d bars", len(objectURIs), len(allBars)),
	}, nil
}

func (r *GCSBarReader) readBarData(ctx context.Context, objName string) ([]BarRow, error) {
	jsonName := strings.Replace(objName, ".parquet", ".json", 1)
	return r.readJSONObject(ctx, jsonName)
}

func (r *GCSBarReader) readJSONObject(ctx context.Context, objName string) ([]BarRow, error) {
	rc, err := r.client.Bucket(r.bucket).Object(objName).NewReader(ctx)
	if err != nil {
		return nil, err
	}
	defer rc.Close()

	var bars []BarRow
	if err := json.NewDecoder(rc).Decode(&bars); err != nil {
		return nil, err
	}
	return bars, nil
}

func (r *GCSBarReader) ListSymbols(ctx context.Context, mkt, freq string) ([]string, error) {
	var prefixes []string
	if freq != "" {
		prefixes = []string{fmt.Sprintf("raw/%s/bars/freq=%s/", strings.ToLower(mkt), freq)}
	} else {
		for _, f := range []string{"5m", "1d", "1m"} {
			prefixes = append(prefixes, fmt.Sprintf("raw/%s/bars/freq=%s/", strings.ToLower(mkt), f))
		}
	}

	seen := make(map[string]bool)
	for _, prefix := range prefixes {
		it := r.client.Bucket(r.bucket).Objects(ctx, &storage.Query{Prefix: prefix})
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
			symbol := strings.TrimSuffix(strings.TrimPrefix(file, "symbol="), ".parquet")
			if symbol != "" && symbol != file {
				seen[symbol] = true
			}
		}
	}

	symbols := make([]string, 0, len(seen))
	for s := range seen {
		symbols = append(symbols, s)
	}
	sort.Strings(symbols)
	return symbols, nil
}
