FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive


RUN apt-get update && apt-get install -y \
    make \
    g++ \
    cmake \
    libsctp-dev \
    lksctp-tools \
    iproute2 \
    iputils-ping \
    net-tools \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /ueransim

COPY UERANSIM/ .

RUN rm -rf build cmake-build-release cmake-build-debug
RUN make

ENV PATH="/ueransim/build:${PATH}"

EXPOSE 38412/sctp
EXPOSE 2152/udp

CMD ["/bin/bash"]
