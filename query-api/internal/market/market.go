package market

import (
	"fmt"
	"strings"
	"time"
)

type Market string

const (
	US     Market = "US"
	CN     Market = "CN"
	HK     Market = "HK"
	CRYPTO Market = "CRYPTO"
)

func ParseMarket(s string) (Market, bool) {
	switch strings.ToUpper(s) {
	case "US":
		return US, true
	case "CN":
		return CN, true
	case "HK":
		return HK, true
	case "CRYPTO":
		return CRYPTO, true
	default:
		return "", false
	}
}

func (m Market) StoragePrefix(dataType string) string {
	return fmt.Sprintf("raw/%s/%s", strings.ToLower(string(m)), dataType)
}

type Bar struct {
	Symbol    string    `json:"symbol" parquet:"symbol"`
	Timestamp time.Time `json:"timestamp" parquet:"timestamp"`
	Open      float64   `json:"open" parquet:"open"`
	High      float64   `json:"high" parquet:"high"`
	Low       float64   `json:"low" parquet:"low"`
	Close     float64   `json:"close" parquet:"close"`
	Volume    int64     `json:"volume" parquet:"volume"`
	Market    string    `json:"market" parquet:"market"`
	Frequency string    `json:"frequency" parquet:"frequency"`
}
