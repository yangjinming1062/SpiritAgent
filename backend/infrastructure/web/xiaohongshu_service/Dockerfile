# syntax=docker/dockerfile:1.6
FROM golang:1.24 AS builder
WORKDIR /src
ARG GOPROXY=https://goproxy.cn,direct
ARG GOSUMDB=sum.golang.google.cn
ENV GOPROXY=${GOPROXY} GOSUMDB=${GOSUMDB}

# v2.5.5's annotated tag resolves to this commit; verify its archive before patching.
ADD https://codeload.github.com/xpzouying/xiaohongshu-mcp/tar.gz/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f /tmp/source.tar.gz
RUN echo "5f970a3d7d95b1fdee21d2a9a6418877b1d258ff08e20bce1216e1f0ea316069  /tmp/source.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && rm /tmp/source.tar.gz
RUN go mod vendor
COPY upstream.patch login_admin.go ./
RUN git apply --unidiff-zero --check upstream.patch && git apply --unidiff-zero upstream.patch \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -mod=vendor -ldflags="-s -w -X main.version=v2.5.5-spiritagent-accounts" -o /out/app .

FROM xpzouying/xiaohongshu-mcp:v2.5.5
COPY --from=builder /out/app /app/app
