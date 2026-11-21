FROM debian:bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake libpqxx-dev libgrpc++-dev protobuf-compiler-grpc libprotobuf-dev libgtest-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --target horizon-core -j"$(nproc)"
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libpqxx-6.4 libgrpc++1.51 libprotobuf32 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/core/horizon-core /usr/local/bin/horizon-core
USER 65532:65532
EXPOSE 50051
ENTRYPOINT ["/usr/local/bin/horizon-core"]
