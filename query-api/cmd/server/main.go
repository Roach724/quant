package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/quant/query-api/internal/handler"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	bucket := os.Getenv("GCS_BUCKET")
	if bucket == "" {
		log.Fatal("GCS_BUCKET environment variable is required")
	}

	h := handler.NewHandler(bucket)
	addr := fmt.Sprintf(":%s", port)
	log.Printf("Query API listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, h))
}
