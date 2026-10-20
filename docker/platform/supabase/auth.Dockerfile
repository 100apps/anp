FROM golang:1.27.0-alpine3.23 as build
ENV GO111MODULE=on
ENV CGO_ENABLED=0
ENV GOOS=linux

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apk add --no-cache make git

WORKDIR /go/src/github.com/supabase/auth

# Pulling dependencies
COPY ./Makefile ./go.* ./
COPY ./internal/forks/godotenv ./internal/forks/godotenv
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; make deps

# Building stuff
COPY . /go/src/github.com/supabase/auth

# Make sure you change the RELEASE_VERSION value before publishing an image.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; RELEASE_VERSION=unspecified make build

# Always use alpine:3 so the latest version is used. This will keep CA certs more up to date.
FROM alpine:3
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; adduser -D -u 1000 supabase

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apk add --no-cache ca-certificates
COPY --from=build /go/src/github.com/supabase/auth/auth /usr/local/bin/auth
COPY --from=build /go/src/github.com/supabase/auth/migrations /usr/local/etc/auth/migrations/
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; ln -s /usr/local/bin/auth /usr/local/bin/gotrue

ENV GOTRUE_DB_MIGRATIONS_PATH /usr/local/etc/auth/migrations

USER supabase
CMD ["auth"]
