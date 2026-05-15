FROM golang:1.23-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY . .
RUN go mod tidy && go mod download
RUN CGO_ENABLED=0 go build -o /server ./cmd/server

FROM gcr.io/distroless/static-debian12
COPY --from=builder /server /server
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["/server"]
